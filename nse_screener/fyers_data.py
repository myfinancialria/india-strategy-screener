"""
Intraday-fresh prices via the Fyers API.

The base pipeline runs on end-of-day NSE bhavcopy. This module overlays the
LIVE (or last-traded) bar for the whole screened universe so that a mid-session
re-run of patterns.py reflects today's prices — without abandoning the offline
single-file model. It does NOT provide fundamentals (no broker does — use
fundamentals.py for those).

How it works:
  1. Authenticate with Fyers (app id + secret + a one-time login; token cached).
  2. Pull quotes in batches of 50 (Fyers `quotes` returns the day's O/H/L/C/V
     and last price — a forming daily candle during market hours).
  3. Upsert today's bar for each symbol into data/prices.parquet, replacing any
     existing row for today. consolidate.py's adjusted history stays intact;
     only the latest bar is refreshed.
  Then re-run patterns.py / chartdata.py (run_all.py does this for you).

Credentials (never commit these — keep them in data/.fyers.json, gitignored):
  { "app_id": "XXXXXXXXXX-100",      # API app id / client_id from the Fyers dashboard
    "secret": "YYYYYYYYYY",          # app secret
    "redirect": "https://127.0.0.1", # redirect URI registered for the app
    "pin": "1234" }                  # your 4-digit PIN  (enables headless renewal)
(env equivalents: FYERS_APP_ID / FYERS_SECRET / FYERS_REDIRECT / FYERS_PIN.)

AUTO-LOGIN (official refresh-token flow): you log in via the browser ONCE; Fyers
returns a refresh token (valid ~15 days) which we save. After that, each day's
access token is minted headlessly from that refresh token + your PIN — no auth-code
paste. When the refresh token expires (~15 days) you do one more browser login.
Tokens are cached in data/.fyers_token.json (gitignored).

⚠️ The refresh token + PIN can renew sessions — keep .fyers.json / .fyers_token.json
local, chmod 600, never commit.

Usage:
  python3 nse_screener/fyers_data.py [prices.parquet] [universe_csv]
  python3 nse_screener/fyers_data.py --auth          # login (headless if refresh token is valid)
  python3 nse_screener/fyers_data.py --authurl       # print the manual login URL
"""
import os, sys, json, time, hashlib, datetime as dt
import urllib.request, urllib.parse, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(HERE, "..")
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
PRICES   = ARGS[0] if len(ARGS) > 0 else os.path.join(ROOT, "data", "prices.parquet")
UNIVERSE = ARGS[1] if len(ARGS) > 1 else None
TOKEN_FILE = os.path.join(ROOT, "data", ".fyers_token.json")
CREDS_FILE = os.path.join(ROOT, "data", ".fyers.json")
BATCH = 50


def creds():
    """Read creds from env, falling back to data/.fyers.json.
    Returns (app_id, secret, redirect, auto) where auto={fy_id,totp,pin} enables headless login."""
    c = {}
    if os.path.exists(CREDS_FILE):
        c = json.load(open(CREDS_FILE))
    app = os.environ.get("FYERS_APP_ID")   or c.get("app_id")
    sec = os.environ.get("FYERS_SECRET")   or c.get("secret")
    red = os.environ.get("FYERS_REDIRECT") or c.get("redirect") or "https://127.0.0.1"
    if not (app and sec):
        sys.exit("Missing Fyers credentials. Set FYERS_APP_ID and FYERS_SECRET "
                 "(env vars) or create data/.fyers.json with {app_id, secret, redirect}.")
    auto = {"fy_id": os.environ.get("FYERS_FY_ID")    or c.get("fy_id"),
            "totp":  os.environ.get("FYERS_TOTP_KEY") or c.get("totp_key"),
            "pin":   os.environ.get("FYERS_PIN")      or c.get("pin")}
    return app, sec, red, auto


# ---------- headless renewal: official refresh-token flow (needs PIN + a seeded refresh token) ----------
def _post(url, payload, auth=None):
    hdrs = {"Content-Type": "application/json", "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"}
    if auth:
        hdrs["Authorization"] = auth
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=hdrs, method="POST")
    ep = url.split("/")[-1]
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:                 # surface Fyers' actual message, not a bare 400
        body = e.read().decode("utf-8", "ignore")[:300]
        raise RuntimeError(f"{ep} -> HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"{ep} -> network error: {e.reason}")

def refresh_login(app, sec, pin, refresh_token):
    """Mint a fresh daily access token from a (≤15-day) refresh token + PIN. Official Fyers v3 flow."""
    if not (refresh_token and pin):
        return None
    app_hash = hashlib.sha256(f"{app}:{sec}".encode()).hexdigest()
    r = _post("https://api-t1.fyers.in/api/v3/validate-refresh-token",
              {"grant_type": "refresh_token", "appIdHash": app_hash,
               "refresh_token": refresh_token, "pin": str(pin)})
    tok = r.get("access_token")
    if not tok:
        raise RuntimeError(f"validate-refresh-token: {r}")
    return tok

def _save_token(app, access_token, refresh_token=None):
    data = {"app_id": app, "access_token": access_token, "date": dt.date.today().isoformat()}
    if refresh_token:
        data["refresh_token"] = refresh_token
    json.dump(data, open(TOKEN_FILE, "w"))
    try: os.chmod(TOKEN_FILE, 0o600)
    except OSError: pass


# ---------- fully headless login: TOTP flow (mimics the browser login each day) ----------
def totp_login(app, sec, red, fy_id, totp_key, pin):
    """Obtain an auth_code with no browser, using FY_ID + TOTP secret + PIN.
    Works around the SEBI-disabled refresh-token API by doing a real login each run."""
    import base64, time
    import requests, pyotp
    def enc(s):
        return base64.b64encode(str(s).encode()).decode()
    S = requests.Session()
    S.headers.update({"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"})
    SEND = "https://api-t2.fyers.in/vagator/v2/send_login_otp_v2"
    VOTP = "https://api-t2.fyers.in/vagator/v2/verify_otp"
    VPIN = "https://api-t2.fyers.in/vagator/v2/verify_pin"
    TOK  = "https://api-t1.fyers.in/api/v3/token"

    r = S.post(SEND, json={"fy_id": enc(fy_id), "app_id": "2"}).json()
    rk = r.get("request_key")
    if not rk:
        raise RuntimeError(f"send_login_otp: {r}")
    # TOTP — retry once across the 30s boundary if the code was rejected
    for attempt in range(2):
        otp = pyotp.TOTP(totp_key).now()
        r = S.post(VOTP, json={"request_key": rk, "otp": otp}).json()
        if r.get("request_key"):
            break
        if attempt == 0:
            time.sleep(31 - int(time.time()) % 30)   # wait for the next TOTP window
        else:
            raise RuntimeError(f"verify_otp: {r}")
    rk = r["request_key"]
    r = S.post(VPIN, json={"request_key": rk, "identity_type": "pin", "identifier": enc(pin)}).json()
    at = (r.get("data") or {}).get("access_token")
    if not at:
        raise RuntimeError(f"verify_pin: {r}")
    short, _, atype = app.partition("-")
    payload = {"fyers_id": fy_id, "app_id": short, "redirect_uri": red, "appType": atype,
               "code_challenge": "", "state": "none", "scope": "", "nonce": "",
               "response_type": "code", "create_cookie": True}
    r = S.post(TOK, headers={"Authorization": f"Bearer {at}"}, json=payload).json()
    url = r.get("Url") or r.get("url")
    if not url:
        raise RuntimeError(f"token: {r}")
    return urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["auth_code"][0]


def get_client(force_login=False):
    try:
        from fyers_apiv3 import fyersModel
    except ImportError:
        sys.exit("fyers-apiv3 not installed.  Run:  pip install fyers-apiv3")
    app, sec, red, auto = creds()
    pin = auto.get("pin")

    cached = {}
    if os.path.exists(TOKEN_FILE):
        cached = json.load(open(TOKEN_FILE))
        if cached.get("app_id") != app:
            cached = {}
    refresh_token = cached.get("refresh_token")

    token = None
    if not force_login and cached.get("date") == dt.date.today().isoformat():
        token = cached.get("access_token")                 # today's token still valid

    # headless daily renewal — official: mint a new token from the stored refresh token + PIN
    if not token and refresh_token and pin:
        try:
            token = refresh_login(app, sec, pin, refresh_token)
            if token:
                _save_token(app, token, refresh_token)
                print("Auto-login OK — minted today's token from the saved refresh token.")
        except Exception as e:
            print("Refresh-token renewal failed (will fall back to manual login):", str(e)[:180])

    # fully headless TOTP login — needs fy_id + totp_key + pin (the real fix for SEBI's disabled refresh)
    if not token and auto.get("fy_id") and auto.get("totp") and pin:
        try:
            code = totp_login(app, sec, red, auto["fy_id"], auto["totp"], pin)
            session = fyersModel.SessionModel(client_id=app, secret_key=sec, redirect_uri=red,
                                              response_type="code", grant_type="authorization_code")
            session.set_token(code)
            resp = session.generate_token()
            token = resp.get("access_token")
            refresh_token = resp.get("refresh_token") or refresh_token
            if token:
                _save_token(app, token, refresh_token)
                print("Auto-login OK — headless TOTP login.")
            else:
                print("TOTP login: no access_token in response:", str(resp)[:160])
        except Exception as e:
            print("TOTP auto-login failed (will fall back to manual):", str(e)[:200])

    # one-time / periodic manual login — seeds a refresh token so future runs are headless (~15 days)
    if not token:
        session = fyersModel.SessionModel(
            client_id=app, secret_key=sec, redirect_uri=red,
            response_type="code", grant_type="authorization_code")
        code = None
        if "--code" in sys.argv:
            code = sys.argv[sys.argv.index("--code") + 1].strip()
        elif os.environ.get("FYERS_AUTH_CODE"):
            code = os.environ["FYERS_AUTH_CODE"].strip()
        if code and "auth_code=" in code:                 # accept a full pasted redirect URL
            code = urllib.parse.parse_qs(urllib.parse.urlparse(code).query).get("auth_code", [code])[0]
        if not code:
            print("\n1) Open this URL, log in, and authorise:\n   " + session.generate_authcode())
            code = input("\n2) Paste the auth_code from the redirected URL here:\n   ").strip()
        session.set_token(code)
        resp = session.generate_token()
        token = resp.get("access_token")
        if not token:
            sys.exit(f"Fyers auth failed: {resp}")
        refresh_token = resp.get("refresh_token") or refresh_token
        _save_token(app, token, refresh_token)
        print("Authenticated." + (" Refresh token saved — future logins are headless (PIN only) for ~15 days."
                                   if refresh_token else " (No refresh token returned.)"))

    return fyersModel.FyersModel(client_id=app, token=token, is_async=False, log_path="/tmp")


def fyers_symbol(sym):       # NSE equity symbol format expected by Fyers
    return f"NSE:{sym}-EQ"


def fetch_quotes(fy, symbols):
    """Return {symbol: bar dict} for the day's forming candle."""
    out = {}
    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i:i + BATCH]
        resp = fy.quotes({"symbols": ",".join(fyers_symbol(s) for s in chunk)})
        if resp.get("s") != "ok":
            print(f"  quotes batch {i//BATCH} error: {str(resp)[:120]}")
            time.sleep(0.4); continue
        for item in resp.get("d", []):
            v = item.get("v", {})
            raw = (item.get("n") or "").replace("NSE:", "").replace("-EQ", "")
            o, h, l = v.get("open_price"), v.get("high_price"), v.get("low_price")
            c = v.get("lp")                    # last price = the live close
            vol = v.get("volume")
            if raw and c:
                out[raw] = dict(open=o or c, high=h or c, low=l or c, close=c, volume=vol or 0)
        time.sleep(0.25)                       # stay well under the rate limit
        print(f"  quotes {min(i+BATCH, len(symbols))}/{len(symbols)}")
    return out


def upsert(prices_path, bars, when):
    import pandas as pd
    p = pd.read_parquet(prices_path, engine="fastparquet")
    ts = pd.Timestamp(when)
    rows = []
    for sym, b in bars.items():
        rows.append(dict(symbol=sym, series="EQ", date=ts,
                         open=b["open"], high=b["high"], low=b["low"], close=b["close"],
                         close_raw=b["close"], volume=b["volume"],
                         turnover_lacs=round(b["close"] * b["volume"] / 1e5, 2),
                         deliv_pct=float("nan")))
    live = pd.DataFrame(rows)
    # drop any existing row for `when` on these symbols, then append the live bar
    mask = (p["date"] == ts) & (p["symbol"].isin(live["symbol"]))
    p = pd.concat([p[~mask], live], ignore_index=True)
    p = p.sort_values(["symbol", "date"]).drop_duplicates(["symbol", "date"], keep="last")
    p.to_parquet(prices_path, index=False)
    return len(live)


def main():
    if "--authurl" in sys.argv:          # just print the login URL, then exit
        from fyers_apiv3 import fyersModel
        app, sec, red, _ = creds()
        s = fyersModel.SessionModel(client_id=app, secret_key=sec, redirect_uri=red,
                                    response_type="code", grant_type="authorization_code")
        print(s.generate_authcode())
        return
    if "--auth" in sys.argv:
        get_client(force_login=True)
        print("Done — token cached. Re-run without --auth to pull prices.")
        return

    import pandas as pd
    if not os.path.exists(PRICES):
        sys.exit(f"{PRICES} not found — run update_data.py + consolidate.py first.")

    if UNIVERSE and os.path.exists(UNIVERSE):
        import csv
        symbols = [r["symbol"].strip().upper() for r in csv.DictReader(open(UNIVERSE))]
    else:
        symbols = sorted(pd.read_parquet(PRICES, engine="fastparquet")["symbol"].unique().tolist())
    print(f"Refreshing live bar for {len(symbols)} symbols via Fyers…")

    fy = get_client()
    bars = fetch_quotes(fy, symbols)
    if not bars:
        sys.exit("No quotes returned — check market hours, token, and symbol coverage.")
    n = upsert(PRICES, bars, dt.date.today().isoformat())
    print(f"Upserted live bar for {n} symbols into {PRICES} "
          f"(as of {dt.datetime.now().strftime('%Y-%m-%d %H:%M')}).")
    print("Now re-run:  python3 nse_screener/patterns.py && python3 nse_screener/chartdata.py")


if __name__ == "__main__":
    main()

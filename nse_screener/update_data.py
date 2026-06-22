"""
Update bhavcopy data.
Primary  : direct download from NSE archives (works on your own machine).
Fallback : git mirror github.com/tilak999/NSE-Data-bank (updated daily).

Usage:  python3 update_data.py [--days 400]
Files land in  ../data/bhavcopy/ ; then run consolidate.py.
"""
import os, sys, subprocess, datetime as dt, urllib.request, shutil, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "..", "data", "bhavcopy")
os.makedirs(DATA, exist_ok=True)

DAYS = 400
if "--days" in sys.argv:
    DAYS = int(sys.argv[sys.argv.index("--days") + 1])

HDRS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

def have(date):
    return os.path.exists(os.path.join(DATA, f"sec_bhavdata_full_{date:%d%m%Y}.csv"))

def fetch_nse(date):
    url = f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{date:%d%m%Y}.csv"
    req = urllib.request.Request(url, headers=HDRS)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            body = r.read()
            if len(body) < 10000:  # holiday / bad file
                return False
            with open(os.path.join(DATA, f"sec_bhavdata_full_{date:%d%m%Y}.csv"), "wb") as f:
                f.write(body)
            return True
    except Exception:
        return False

def fetch_mirror():
    """Sparse-clone the daily-updated mirror and copy missing files."""
    tmp = tempfile.mkdtemp()
    try:
        subprocess.run(["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                        "https://github.com/tilak999/NSE-Data-bank.git", tmp],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", tmp, "sparse-checkout", "set", "data"],
                       check=True, capture_output=True)
        src = os.path.join(tmp, "data")
        copied = 0
        cutoff = dt.date.today() - dt.timedelta(days=DAYS)
        for f in os.listdir(src):
            if not f.startswith("sec_bhavdata_full_"):
                continue
            d = f[len("sec_bhavdata_full_"):-4]
            try:
                date = dt.date(int(d[4:8]), int(d[2:4]), int(d[0:2]))
            except ValueError:
                continue
            if date >= cutoff and not os.path.exists(os.path.join(DATA, f)):
                shutil.copy(os.path.join(src, f), DATA)
                copied += 1
        print(f"mirror: copied {copied} files")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

def main():
    today = dt.date.today()
    missing = []
    for i in range(DAYS):
        d = today - dt.timedelta(days=i)
        if d.weekday() < 5 and not have(d):
            missing.append(d)
    print(f"missing weekday files: {len(missing)}")
    if not missing:
        return
    ok = fail = 0
    for d in missing:
        if fetch_nse(d):
            ok += 1
        else:
            fail += 1
        if fail >= 5 and ok == 0:   # NSE unreachable -> use mirror
            print("NSE direct failed; falling back to GitHub mirror...")
            fetch_mirror()
            return
    print(f"NSE direct: downloaded {ok} files ({fail} holidays/misses)")

if __name__ == "__main__":
    main()

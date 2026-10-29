import io, time
import requests
import yfinance as yf
import pandas as pd


def read_list(path):
    try:
        out = []
        for line in open(path):
            t = line.split("#")[0].strip()
            if t:
                out.append(t)
        return out
    except FileNotFoundError:
        return []


def wiki_tickers(url, cols):
    try:
        html = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (kurs-bot)"},
                            timeout=30).text
        for tbl in pd.read_html(io.StringIO(html)):
            for c in cols:
                if c in tbl.columns:
                    return [str(x).replace(".", "-") for x in tbl[c].dropna()]
    except Exception as e:
        print("Wikipedia-feil:", url, e)
    return []


core = read_list("tickers.txt")      # regelporteføljens univers
extra = read_list("extra.txt")       # manuell liste (Satsporteføljen)
sp500 = wiki_tickers("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", ["Symbol"])
ndx = wiki_tickers("https://en.wikipedia.org/wiki/Nasdaq-100", ["Ticker", "Symbol"])
print("S&P 500:", len(sp500), "Nasdaq-100:", len(ndx), "extra:", len(extra))

us_core = {t for t in core if "." not in t and not t.startswith("^")}
screen_u = sorted(set(extra) | set(sp500) | set(ndx) | us_core)
alltk = sorted(set(core) | set(screen_u))

raw = yf.download(alltk, period="2y", interval="1d", auto_adjust=False,
                  progress=False, threads=True)
adj = raw["Adj Close"].dropna(how="all")

# ---------- prices.csv (regelporteføljens univers, justerte kurser) ----------
cols = [c for c in core if c in adj.columns]
df = adj[cols].copy()
df.index = df.index.strftime("%Y-%m-%d")
df.round(4).to_csv("prices.csv")

# ---------- ohlc.csv (siste 10 handelsdager, alle tickere) ----------
recs = []
for t in alltk:
    try:
        d = pd.DataFrame({k: raw[k][t] for k in ["Open", "High", "Low", "Close"]})
    except KeyError:
        continue
    for date, r in d.tail(10).dropna().iterrows():
        recs.append([date.strftime("%Y-%m-%d"), t, *r.round(4)])
pd.DataFrame(recs, columns=["date", "ticker", "open", "high", "low", "close"]
             ).to_csv("ohlc.csv", index=False)

# ---------- metrics.csv + regime.csv (regelporteføljen) ----------
rows = []
for t in df.columns:
    if t.startswith("^"):
        continue
    s = df[t].dropna()
    if len(s) < 253:
        continue
    rows.append({
        "ticker": t,
        "last": round(s.iloc[-1], 4),
        "mom_12_1": round(s.iloc[-22] / s.iloc[-253] - 1, 4),
        "sma50": round(s.tail(50).mean(), 4),
        "sma200": round(s.tail(200).mean(), 4),
    })
m = pd.DataFrame(rows)
m["above50"] = m["last"] > m["sma50"]
m["above200"] = m["last"] > m["sma200"]
m["rank"] = m["mom_12_1"].rank(ascending=False).astype(int)

caps, curs = {}, {}
for t in m["ticker"]:
    try:
        fi = yf.Ticker(t).fast_info
        caps[t], curs[t] = fi["market_cap"], fi["currency"]
    except Exception:
        caps[t], curs[t] = None, None
m["mcap"] = m["ticker"].map(caps)
m["cur"] = m["ticker"].map(curs)

nordic = m[m["ticker"].str.contains(".", regex=False)]
pd.DataFrame([{"date": df.index[-1], "n": len(nordic),
               "pct_above200": round(nordic["above200"].mean(), 3)}]
             ).to_csv("regime.csv", index=False)
m.sort_values("rank").to_csv("metrics.csv", index=False)

# ---------- screen.csv (Satsporteføljen: bevegelse, volum og short-data) ----------
cl, hi, vol = raw["Close"], raw["High"], raw["Volume"]
srows = []
for t in screen_u:
    if t not in cl.columns:
        continue
    s = cl[t].dropna()
    if len(s) < 30:
        continue
    v = vol[t].dropna()
    av = v.tail(20).mean() if len(v) >= 20 else None
    srows.append({
        "ticker": t,
        "last": round(s.iloc[-1], 4),
        "chg_1d": round(s.iloc[-1] / s.iloc[-2] - 1, 4),
        "chg_5d": round(s.iloc[-1] / s.iloc[-6] - 1, 4),
        "chg_1m": round(s.iloc[-1] / s.iloc[-22] - 1, 4),
        "from_52w_high": round(s.iloc[-1] / hi[t].dropna().tail(252).max() - 1, 4),
        "avg_vol_20": int(av) if av else None,
        "vol_ratio": round(v.iloc[-1] / av, 2) if av else None,
    })
sc = pd.DataFrame(srows)

short = {}
for t in sc["ticker"]:
    if "." in t or t.startswith("^"):
        continue
    try:
        i = yf.Ticker(t).info
        short[t] = (i.get("shortPercentOfFloat"), i.get("shortRatio"),
                    i.get("floatShares"), i.get("marketCap"))
    except Exception:
        pass
    time.sleep(0.1)
sc["short_pct_float"] = sc["ticker"].map(lambda t: short.get(t, (None,) * 4)[0])
sc["days_to_cover"] = sc["ticker"].map(lambda t: short.get(t, (None,) * 4)[1])
sc["float_shares"] = sc["ticker"].map(lambda t: short.get(t, (None,) * 4)[2])
sc["mcap_usd"] = sc["ticker"].map(lambda t: short.get(t, (None,) * 4)[3])
sc.to_csv("screen.csv", index=False)
print("ferdig:", len(m), "metrics,", len(sc), "screen")

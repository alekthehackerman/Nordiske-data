import yfinance as yf, pandas as pd

tickers = [l.strip() for l in open("tickers.txt")
           if l.strip() and not l.startswith("#")]
raw = yf.download(tickers, period="2y", interval="1d",
                  auto_adjust=False, progress=False)

# Justerte sluttkurser til momentum og snitt
df = raw["Adj Close"].dropna(how="all")
df.index = df.index.strftime("%Y-%m-%d")
df.round(4).to_csv("prices.csv")

# Åpning, høy, lav, slutt siste 10 dager (til ordrefyll)
recs = []
for t in tickers:
    try:
        d = pd.DataFrame({k: raw[k][t] for k in ["Open", "High", "Low", "Close"]})
    except KeyError:
        continue
    for date, r in d.tail(10).dropna().iterrows():
        recs.append([date.strftime("%Y-%m-%d"), t, *r.round(4)])
pd.DataFrame(recs, columns=["date", "ticker", "open", "high", "low", "close"]
             ).to_csv("ohlc.csv", index=False)

# Momentum og snitt
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

import io, os, time
from datetime import datetime, timedelta, timezone
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

def group_of(t):
    if t.startswith("^"):
        return "idx"
    return t.split(".")[-1] if "." in t else "US"

# Børsene stenger (UTC, vinter): Europa ca. 16:30, USA 21:00. Rader datert i dag før dette
# er ufullstendige (bare enkeltkurser under handel) og skal ikke lagres som dagens kurs.
NOW = datetime.now(timezone.utc)
TODAY = NOW.date()
CUT = {"US": 21 * 60 + 20, "idx": 21 * 60 + 20}   # minutter etter midnatt UTC; resten = Europa
EU_CUT = 16 * 60 + 45


def expected_date(g):
    """Siste handelsdag som burde være komplett akkurat nå (ignorerer helligdager)."""
    cut = CUT.get(g, EU_CUT)
    d = TODAY if NOW.hour * 60 + NOW.minute >= cut else TODAY - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def coverage(part, tk, d):
    try:
        close = part["Close"]
    except KeyError:
        return 0.0
    cols = [c for c in tk if c in close.columns]
    if not cols:
        return 0.0
    rows = close.loc[[i for i in close.index if i.date() == d], cols]
    return float(rows.notna().any().sum()) / len(tk) if len(rows) else 0.0


parts = []
for g in sorted({group_of(t) for t in alltk}):
    tk = [t for t in alltk if group_of(t) == g]
    part = yf.download(tk, period="2y", interval="1d",
                       auto_adjust=False, progress=False, threads=True)
    exp = expected_date(g)
    cov = coverage(part, tk, exp)
    print(f"{g}: {len(tk)} tickere, forventet {exp}, dekning {cov:.0%}")
    if cov < 0.8 and g not in ("idx",):
        # Hull (Yahoo gir noen ganger ikke siste dag over natten): prøv igjen med eksplisitte datoer
        time.sleep(5)
        try:
            alt = yf.download(tk, start=(exp - timedelta(days=10)).isoformat(),
                              end=(TODAY + timedelta(days=2)).isoformat(), interval="1d",
                              auto_adjust=False, progress=False, threads=False)
            cov2 = coverage(alt, tk, exp)
            print(f"{g}: nytt forsøk, dekning {cov2:.0%}")
            if cov2 > cov:
                part = part.combine_first(alt)
        except Exception as e:
            print(f"{g}: nytt forsøk feilet: {e}")
    cut = CUT.get(g, EU_CUT)
    if NOW.hour * 60 + NOW.minute < cut:
        part = part[[i.date() != TODAY for i in part.index]]
    parts.append(part)
raw = pd.concat(parts, axis=1).sort_index()
adj = raw["Adj Close"].dropna(how="all")

# ---------- prices.csv (regelporteføljens univers, justerte kurser) ----------
cols = [c for c in core if c in adj.columns]
df = adj[cols].copy()
df.index = df.index.strftime("%Y-%m-%d")
# Slå sammen med forrige fil: nye verdier vinner, hull fylles fra forrige kjøring
if os.path.exists("prices.csv"):
    old = pd.read_csv("prices.csv", index_col=0)
    df = df.combine_first(old)
    df = df[[c for c in cols if c in df.columns]]
df = df.sort_index().dropna(how="all").tail(520)   # fjern helg-/valutarader uten aksjekurser
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
new_o = pd.DataFrame(recs, columns=["date", "ticker", "open", "high", "low", "close"])
if os.path.exists("ohlc.csv"):
    old_o = pd.read_csv("ohlc.csv")
    new_o = pd.concat([new_o, old_o]).drop_duplicates(["date", "ticker"], keep="first")
cnt_d = new_o.groupby("date")["ticker"].nunique()
real_dates = sorted(cnt_d[cnt_d >= 20].index)      # dager med bare valuta (helg) telles ikke
new_o = new_o[new_o["date"].isin(real_dates)]
keep_dates = real_dates[-10:]
new_o = new_o[new_o["date"].isin(keep_dates)].sort_values(["date", "ticker"])
new_o.to_csv("ohlc.csv", index=False)

# status.csv: siste komplette dato per børs (>= 80 % av tickerne har rad)
new_o["grp"] = new_o["ticker"].map(group_of)
status_rows = []
for g, sub in new_o.groupby("grp"):
    n = sub["ticker"].nunique()
    cnt = sub.groupby("date")["ticker"].nunique()
    ok = cnt[cnt >= 0.8 * n]
    status_rows.append({"group": g, "tickers": n,
                  "last_complete_date": ok.index.max() if len(ok) else None})
st = pd.DataFrame(status_rows)
st["fetched_utc"] = NOW.strftime("%Y-%m-%d %H:%M")
st.to_csv("status.csv", index=False)

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
nordic_cols = [c for c in df.columns if "." in c and not c.startswith("^")]
nordic_date = df[nordic_cols].dropna(how="all").index[-1]
pd.DataFrame([{"date": nordic_date, "n": len(nordic),
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

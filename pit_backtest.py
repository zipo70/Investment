"""
pit_backtest.py — tester MODEL A på andre universer end vores håndplukkede.

1. S&P 500 POINT-IN-TIME: på hver dato må modellen kun vælge blandt de
   selskaber, der FAKTISK var med i S&P 500 den dag (datasæt:
   github.com/fja05680/sp500, MIT-licens). Selskaber der senere røg ud er
   med — så længe Yahoo stadig har kurser på dem.
2. JAPAN: ~60 af de største japanske selskaber (dagens liste — dvs. stadig
   overlevelses-bias), i yen, mod TOPIX-ETF'en 1306.T.
3. VORES UNIVERS til sammenligning (samme motor).

For hvert univers sammenlignes model A med:
  * et indeks (SPY / 1306.T / ACWI), og
  * LIGEVÆGTET hele universet, rebalanceret samme dage. Det er den vigtigste
    kontrol: forskellen model A − ligevægt måler selve momentum-effekten,
    mens fejl i universet (overlevelses-bias) rammer begge ens.

Motor: signal på lukkekurs, handel næste dags åbning, 0,10 % pr. handel,
lokal valuta, top 5 efter 6-1 mdr. momentum over 200-dages snit,
rebalancering hver 63. handelsdag (13 forskudte startuger), stop −15 %
tjekket ugentligt.

Output: output/pit_backtest.md og .json
Research-værktøj, ikke finansiel rådgivning.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time

import numpy as np
import pandas as pd

SP500_URL = ("https://raw.githubusercontent.com/fja05680/sp500/master/"
             "S%26P%20500%20Historical%20Components%20%26%20Changes%20(Updated).csv")
DATA_START = "2009-01-01"
TRADE_START = "2010-06-01"
COST = 0.0010
N, LOOKBACK, SKIP, SMA, STOP, REB = 5, 126, 21, 200, 0.15, 63

JAPAN = [
    "7203.T", "6758.T", "6861.T", "8306.T", "8035.T", "4063.T", "9984.T", "6501.T", "6098.T",
    "9983.T", "7974.T", "9433.T", "4568.T", "8316.T", "8411.T", "4502.T", "7267.T", "8001.T",
    "8058.T", "8031.T", "8766.T", "6367.T", "6954.T", "6273.T", "6981.T", "7741.T", "4519.T",
    "4661.T", "6857.T", "6902.T", "7751.T", "3382.T", "9432.T", "9434.T", "4503.T", "5108.T",
    "6301.T", "6503.T", "6594.T", "4543.T", "2914.T", "4452.T", "6752.T", "6723.T", "6920.T",
    "6146.T", "8053.T", "8002.T", "8591.T", "8604.T", "7011.T", "7012.T", "6702.T", "6701.T",
    "4901.T", "6178.T", "9020.T", "9022.T", "8801.T", "8802.T", "2802.T", "4911.T", "7201.T",
    "7269.T", "5401.T", "1605.T", "8725.T", "8750.T",
]


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def yahoo_ticker(t: str) -> str:
    return t.replace(".", "-")


def download(tickers: list[str], batch: int = 80) -> tuple[pd.DataFrame, pd.DataFrame]:
    import yfinance as yf
    opens, closes = {}, {}
    for i in range(0, len(tickers), batch):
        chunk = tickers[i:i + batch]
        for attempt in range(3):
            try:
                df = yf.download(chunk, start=DATA_START, auto_adjust=True, progress=False,
                                 threads=True, group_by="ticker")
                break
            except Exception as e:
                print(f"  batch {i}: forsøg {attempt + 1} fejlede ({e})")
                time.sleep(5 * (attempt + 1))
        else:
            continue
        for t in chunk:
            try:
                sub = df[t] if isinstance(df.columns, pd.MultiIndex) else df
                c = sub["Close"].dropna()
                if len(c) > 300:
                    closes[t], opens[t] = sub["Close"], sub["Open"]
            except KeyError:
                pass
        print(f"  hentet {min(i + batch, len(tickers))}/{len(tickers)} · med data: {len(closes)}")
        time.sleep(2)
    C = pd.DataFrame(closes).sort_index()
    O = pd.DataFrame(opens).reindex(C.index)
    C.index = pd.DatetimeIndex(C.index).tz_localize(None)
    O.index = C.index
    return O, C


def sp500_membership() -> pd.DataFrame:
    m = pd.read_csv(SP500_URL, parse_dates=["date"])
    m = m[m["date"] >= DATA_START].copy()
    m["set"] = m["tickers"].apply(lambda s: {yahoo_ticker(x) for x in s.split(",")})
    return m[["date", "set"]].set_index("date")


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

def run(O: pd.DataFrame, C: pd.DataFrame, offset_weeks: int, member_fn=None, mode: str = "momentum"):
    """mode: 'momentum' (model A) eller 'equal' (ligevægt af alle kvalificerede
    medlemmer — kontrolgruppe, ingen momentum- eller trendfilter)."""
    dates = C.index
    sma = C.rolling(SMA, min_periods=SMA).mean()
    mom = C.shift(SKIP) / C.shift(LOOKBACK) - 1
    start = int(dates.searchsorted(pd.Timestamp(TRADE_START))) + 5 * offset_weeks
    Cv, Ov = C.to_numpy(float), O.to_numpy(float)
    Fv = C.ffill().to_numpy(float)  # sidste kendte kurs — til værdiansættelse/salg af afnoterede
    cols = list(C.columns)
    ci = {t: k for k, t in enumerate(cols)}

    cash, pos, eq = 1.0, {}, {}
    last_reb = None
    for i in range(start, len(dates) - 1, 5):
        val = cash + sum(p["sh"] * Fv[i, ci[t]] for t, p in pos.items() if not np.isnan(Fv[i, ci[t]]))
        eq[dates[i]] = val
        e = i + 1
        sells, buys = [], []
        if mode == "momentum":
            for t, p in pos.items():
                c = Cv[i, ci[t]]
                if not np.isnan(c) and c / p["entry"] - 1 <= -STOP:
                    sells.append(t)
        if last_reb is None or i - last_reb >= REB:
            last_reb = i
            members = member_fn(dates[i]) if member_fn else set(cols)
            row_c, row_s, row_m = C.iloc[i], sma.iloc[i], mom.iloc[i]
            ok = [t for t in cols if t in members and not np.isnan(row_c[t]) and not np.isnan(row_m[t])]
            if mode == "momentum":
                elig = [t for t in ok if row_c[t] > row_s[t]]
                target = sorted(elig, key=lambda t: row_m[t], reverse=True)[:N]
                n_slots = N
            else:
                target, n_slots = ok, max(len(ok), 1)
            sells += [t for t in pos if t not in target and t not in sells]
            buys = [t for t in target if t not in pos]
            if mode == "equal":
                sells, buys = list(pos), list(target)  # fuld rebalancering til ligevægt
        for t in sells:
            p = pos.pop(t)
            px = Ov[e, ci[t]]
            if np.isnan(px):  # ingen handel i morgen (fx afnoteret) — sidste kendte kurs
                px = Fv[i, ci[t]] if not np.isnan(Fv[i, ci[t]]) else 0.0
            cash += p["sh"] * px * (1 - COST)
        if buys:
            total = cash + sum(p["sh"] * (Ov[e, ci[t]] if not np.isnan(Ov[e, ci[t]]) else np.nan_to_num(Fv[i, ci[t]]))
                               for t, p in pos.items())
            slot = total / n_slots
            for t in buys:
                px = Ov[e, ci[t]]
                if np.isnan(px) or px <= 0:
                    continue
                spend = min(slot, cash)
                if spend <= 0:
                    break
                pos[t] = {"sh": spend * (1 - COST) / px, "entry": px}
                cash -= spend
    return pd.Series(eq)


def stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return {"cagr": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100,
            "dd": float((eq / eq.cummax() - 1).min() * 100),
            "sharpe": float(r.mean() / r.std() * np.sqrt(52)) if r.std() > 0 else np.nan}


def evaluate(label, O, C, bench: pd.Series, member_fn=None, extra=None) -> dict:
    rows = []
    for k in range(13):
        a = run(O, C, k, member_fn, "momentum")
        b = run(O, C, k, member_fn, "equal")
        idx = bench.reindex(a.index).ffill()
        sa, sb, si = stats(a), stats(b), stats(idx)
        rows.append({"a": sa["cagr"], "a_dd": sa["dd"], "a_sh": sa["sharpe"],
                     "ew": sb["cagr"], "ew_dd": sb["dd"], "ew_sh": sb["sharpe"],
                     "idx": si["cagr"], "idx_dd": si["dd"], "idx_sh": si["sharpe"],
                     "fra": str(a.index[0].date()), "til": str(a.index[-1].date())})
    df = pd.DataFrame(rows)
    med = lambda c: round(float(df[c].median()), 2 if c.endswith("sh") else 1)
    out = {"univers": label, "periode": f"{df.fra.iloc[0]} → {df.til.iloc[0]}",
           **{c: med(c) for c in ["a", "a_dd", "a_sh", "ew", "ew_dd", "ew_sh", "idx", "idx_dd", "idx_sh"]},
           "a_p25": round(float(df.a.quantile(.25)), 1), "a_p75": round(float(df.a.quantile(.75)), 1),
           "slaar_indeks_pct": round(float((df.a > df.idx).mean() * 100)),
           "slaar_ligevaegt_pct": round(float((df.a > df.ew).mean() * 100)),
           "momentum_effekt_pp": round(float((df.a - df.ew).median()), 1)}
    if extra:
        out.update(extra)
    print(out)
    return out


def main():
    results = []

    # 1) S&P 500 point-in-time
    print("S&P 500: henter medlemsliste...")
    mem = sp500_membership()
    all_t = sorted(set().union(*mem["set"]))
    O, C = download(all_t + ["SPY"])
    spy = C.pop("SPY")
    O = O.drop(columns=["SPY"], errors="ignore")
    mdates = mem.index

    def member_fn(d):
        k = mdates.searchsorted(d, side="right") - 1
        return mem["set"].iloc[max(k, 0)]

    # dækning: andel af medlemmer på rebalanceringsdage med kursdata
    sample = C.index[C.index.searchsorted(pd.Timestamp(TRADE_START))::63]
    cov = [len(member_fn(d) & set(C.columns[C.loc[d].notna()])) / max(len(member_fn(d)), 1) for d in sample]
    results.append(evaluate("S&P 500 point-in-time", O, C, spy, member_fn,
                            {"indeks": "SPY", "tickere_i_alt": len(all_t), "med_kursdata": C.shape[1],
                             "daekning_median_pct": round(float(np.median(cov)) * 100, 1),
                             "daekning_min_pct": round(float(np.min(cov)) * 100, 1)}))

    # 2) Japan
    print("Japan: henter kurser...")
    Oj, Cj = download(JAPAN + ["1306.T"])
    topix = Cj.pop("1306.T")
    Oj = Oj.drop(columns=["1306.T"], errors="ignore")
    results.append(evaluate("Japan (~65 største, dagens liste)", Oj, Cj, topix, None,
                            {"indeks": "TOPIX-ETF 1306.T", "med_kursdata": Cj.shape[1]}))

    # 3) Vores univers (samme motor)
    from universe import full_universe, BENCHMARK
    ours = [t for t in full_universe() if t != BENCHMARK]
    Ou, Cu = download(ours + ["ACWI"])
    acwi = Cu.pop("ACWI")
    Ou = Ou.drop(columns=["ACWI"], errors="ignore")
    results.append(evaluate("Vores univers (79, håndplukket)", Ou, Cu, acwi, None,
                            {"indeks": "ACWI", "med_kursdata": Cu.shape[1]}))

    out = {"genereret_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "resultater": results}
    os.makedirs("output", exist_ok=True)
    with open("output/pit_backtest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    md = [f"# Model A på andre universer — {out['genereret_utc']}", "",
          "Lokal valuta, næste dags åbning, 0,10 % pr. handel, 13 startuger, medianer.", "",
          "| Univers | Model A | Ligevægt (samme univers) | Indeks | Momentum-effekt | Slår ligevægt | Slår indeks |",
          "|---|---|---|---|---|---|---|"]
    for r in results:
        md.append(f"| {r['univers']} ({r['periode']}) | {r['a']}% (DD {r['a_dd']}%, Sh {r['a_sh']}) | "
                  f"{r['ew']}% (DD {r['ew_dd']}%) | {r['idx']}% {r['indeks']} (DD {r['idx_dd']}%) | "
                  f"{r['momentum_effekt_pp']} pp | {r['slaar_ligevaegt_pct']}% | {r['slaar_indeks_pct']}% |")
    sp = results[0]
    md += ["", f"S&P 500-datadækning: {sp['med_kursdata']} af {sp['tickere_i_alt']} historiske medlemmer har "
           f"kurser hos Yahoo; på rebalanceringsdagene havde median {sp['daekning_median_pct']}% "
           f"(min {sp['daekning_min_pct']}%) af medlemmerne data. Manglende = typisk opkøbte/konkursramte.",
           "", "Research-værktøj, ikke finansiel rådgivning."]
    with open("output/pit_backtest.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()

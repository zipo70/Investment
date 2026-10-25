"""
improve_strategy.py — tester teoribaserede forbedringer af swing-strategien
med en ærlig trænings-/testopdeling.

  Træning: 2015 → 2022-12-31   (her vurderes ændringerne)
  Test:    2023-01-01 → i dag  (kørt én gang, ingen justering bagefter)

Hver variant køres med 21 forskudte startdatoer (hver 3. af 63), så vi ser
fordelingen frem for én heldig kørsel. Sammenlignes med ACWI over præcis
samme vindue.

Den forudvalgte kombination ("Kombi") er fastlagt FØR resultaterne blev set:
12-1 momentum + markedsfilter + 10 positioner + buffer.

Output: output/improve_strategy.md og .json
Research-værktøj, ikke finansiel rådgivning.
"""

import datetime as dt
import json
import os
from dataclasses import replace, asdict

import numpy as np
import pandas as pd

from config import Config
from data_fetch import load_prices
from backtest_v2 import V2Options, run_v2, stats, WARMUP

TRAIN_END = "2022-12-31"
TEST_START = "2023-01-01"
OFFSETS = range(0, 63, 3)

BASE = V2Options()
KOMBI = replace(BASE, momentum_lookback_days=252, market_filter=True, max_positions=10, buffer_mult=2.0)
VARIANTS = [
    ("Original (6-1, 5 aktier)", BASE),
    ("1. 12-1 momentum", replace(BASE, momentum_lookback_days=252)),
    ("2. Markedsfilter", replace(BASE, market_filter=True)),
    ("3. Volatilitetsstyring", replace(BASE, vol_target=0.20)),
    ("4a. 10 aktier", replace(BASE, max_positions=10)),
    ("4b. 15 aktier", replace(BASE, max_positions=15)),
    ("5. Buffer (10 aktier)", replace(BASE, max_positions=10, buffer_mult=2.0)),
    ("6. Jævn momentum", replace(BASE, smooth_momentum=True)),
    ("Kombi (1+2+4a+5)", KOMBI),
    ("Kombi + volatilitetsstyring", replace(KOMBI, vol_target=0.20)),
]


def evaluate(prices: pd.DataFrame, o: V2Options) -> dict:
    rows = []
    for k in OFFSETS:
        try:
            res = run_v2(prices.iloc[k:], o)
        except RuntimeError:
            continue
        s, b = stats(res["equity"]), stats(res["bench"])
        rows.append({"cagr": s["cagr"] * 100, "dd": s["max_dd"] * 100, "sharpe": s["sharpe"],
                     "b_cagr": b["cagr"] * 100, "b_dd": b["max_dd"] * 100, "b_sharpe": b["sharpe"],
                     "handler": len(res["trades"])})
    df = pd.DataFrame(rows)
    med = df.median()
    return {
        "koersler": len(df),
        "cagr_median": round(med.cagr, 1),
        "cagr_p25": round(float(df.cagr.quantile(0.25)), 1),
        "cagr_p75": round(float(df.cagr.quantile(0.75)), 1),
        "maxdd_median": round(med.dd, 1),
        "sharpe_median": round(med.sharpe, 2),
        "acwi_cagr": round(med.b_cagr, 1), "acwi_maxdd": round(med.b_dd, 1), "acwi_sharpe": round(med.b_sharpe, 2),
        "slaar_acwi_pct": round(float((df.cagr > df.b_cagr).mean() * 100), 0),
        "merafkast_median": round(float((df.cagr - df.b_cagr).median()), 1),
        "handler_median": int(med.handler),
    }


def main():
    cfg = Config()
    prices = load_prices(cfg, force=True)
    test_pos = int(prices.index.searchsorted(pd.Timestamp(TEST_START)))
    train = prices.loc[:TRAIN_END]
    test = prices.iloc[max(0, test_pos - WARMUP):]
    print(f"Træning {train.index[WARMUP].date()} → {train.index[-1].date()}, "
          f"test {test.index[WARMUP].date()} → {test.index[-1].date()}")

    results = {}
    for name, o in VARIANTS:
        results[name] = {"traening": evaluate(train, o), "test": evaluate(test, o)}
        print(name, results[name])

    out = {"genereret_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "traening": f"{train.index[WARMUP].date()} → {train.index[-1].date()}",
           "test": f"{test.index[WARMUP].date()} → {test.index[-1].date()}",
           "varianter": {n: asdict(o) for n, o in VARIANTS}, "resultater": results}
    os.makedirs("output", exist_ok=True)
    with open("output/improve_strategy.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    md = [f"# Forbedringsforsøg — {out['genereret_utc']}", "",
          f"Træning {out['traening']} · Test {out['test']} · 21 startdatoer pr. variant · medianer", ""]
    for part, title in (("traening", "Træning"), ("test", "Test (out-of-sample)")):
        first = next(iter(results.values()))[part]
        md += [f"## {title} — ACWI: {first['acwi_cagr']}%/år, max DD {first['acwi_maxdd']}%, Sharpe {first['acwi_sharpe']}", "",
               "| Variant | Årligt | Spænd (25–75 %) | Max DD | Sharpe | Slår ACWI | Handler |",
               "|---|---|---|---|---|---|---|"]
        for name, r in results.items():
            m = r[part]
            md.append(f"| {name} | {m['cagr_median']}% | {m['cagr_p25']}–{m['cagr_p75']}% | {m['maxdd_median']}% | "
                      f"{m['sharpe_median']} | {m['slaar_acwi_pct']:.0f}% | {m['handler_median']} |")
        md.append("")
    md.append("Forbehold: dagens univers bruges bagud (overlevelses-bias), ingen valutaeffekt. Research, ikke rådgivning.")
    with open("output/improve_strategy.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()

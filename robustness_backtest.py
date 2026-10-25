"""
robustness_backtest.py — hvor følsom er den originale swing-strategi over for
HVORNÅR rebalanceringen falder?

backtest.py rebalancerer hver 63. handelsdag regnet fra første handelsdag.
En enkelt kørsel er derfor én tilfældig "fase". Her køres den UÆNDREDE
backtest 63 gange, hvor starten forskydes 0..62 handelsdage, og fordelingen
af resultater sammenlignes med ACWI over præcis samme vindue.

To perioder: hele historikken (fra 2015) og de seneste ~3 år (fra 2023-11-21).
Output: output/robustness_backtest.md og .json

Research-værktøj, ikke finansiel rådgivning.
"""

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from config import Config
from data_fetch import load_prices
from backtest import run_backtest
from metrics import summary

COMPARE_FROM = "2023-11-21"


def run_offsets(prices: pd.DataFrame, cfg: Config, base_start: int) -> pd.DataFrame:
    rows = []
    for k in range(cfg.rebalance_every_days):
        sub = prices.iloc[base_start + k:]
        try:
            res = run_backtest(sub, cfg)
        except RuntimeError:
            continue
        s = summary(res["equity_curve"], res["benchmark_curve"], res["trade_log"])
        rows.append({
            "offset": k, "fra": str(res["equity_curve"].index[0].date()),
            "cagr": s["cagr"] * 100, "acwi_cagr": s["bench_cagr"] * 100,
            "max_dd": s["max_drawdown"] * 100, "acwi_max_dd": s["bench_max_drawdown"] * 100,
            "sharpe": s["sharpe"], "acwi_sharpe": s["bench_sharpe"],
            "slaar_acwi": s["cagr"] > s["bench_cagr"],
        })
    return pd.DataFrame(rows)


def describe(df: pd.DataFrame) -> dict:
    q = lambda c: {k: round(float(v), 1) for k, v in
                   zip(["min", "p25", "median", "p75", "max"], np.percentile(df[c], [0, 25, 50, 75, 100]))}
    return {
        "koersler": len(df),
        "cagr": q("cagr"), "acwi_cagr_median": round(float(df.acwi_cagr.median()), 1),
        "max_dd": q("max_dd"), "acwi_max_dd_median": round(float(df.acwi_max_dd.median()), 1),
        "sharpe_median": round(float(df.sharpe.median()), 2),
        "acwi_sharpe_median": round(float(df.acwi_sharpe.median()), 2),
        "andel_slaar_acwi_pct": round(float(df.slaar_acwi.mean() * 100), 1),
        "merafkast_median_pct_point": round(float((df.cagr - df.acwi_cagr).median()), 1),
    }


def main():
    cfg = Config()
    prices = load_prices(cfg, force=True)
    needed = max(cfg.trend_sma_window, cfg.momentum_lookback_days) + 1
    recent_start = max(0, int(prices.index.searchsorted(pd.Timestamp(COMPARE_FROM))) - needed)

    periods = {}
    for label, start in ((f"Hele perioden (fra {cfg.start_date})", 0),
                         (f"Seneste ~3 år (fra {COMPARE_FROM})", recent_start)):
        df = run_offsets(prices, cfg, start)
        periods[label] = {"opsummering": describe(df), "koersler": df.round(2).to_dict("records")}
        print(label, periods[label]["opsummering"])

    out = {"genereret_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "perioder": periods}
    os.makedirs("output", exist_ok=True)
    with open("output/robustness_backtest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    md = [f"# Robusthedstest — 63 startdatoer — {out['genereret_utc']}", ""]
    for label, p in periods.items():
        s = p["opsummering"]
        md += [f"## {label} ({s['koersler']} kørsler)", "",
               "| | Min | 25 % | Median | 75 % | Max | ACWI (median) |", "|---|---|---|---|---|---|---|",
               f"| Årligt afkast | {s['cagr']['min']}% | {s['cagr']['p25']}% | {s['cagr']['median']}% | "
               f"{s['cagr']['p75']}% | {s['cagr']['max']}% | {s['acwi_cagr_median']}% |",
               f"| Max drawdown | {s['max_dd']['min']}% | {s['max_dd']['p25']}% | {s['max_dd']['median']}% | "
               f"{s['max_dd']['p75']}% | {s['max_dd']['max']}% | {s['acwi_max_dd_median']}% |",
               "", f"Slår ACWI i {s['andel_slaar_acwi_pct']}% af kørslerne · median merafkast "
               f"{s['merafkast_median_pct_point']} %-point/år · Sharpe median {s['sharpe_median']} "
               f"(ACWI {s['acwi_sharpe_median']})", ""]
    md.append("Forbehold: dagens univers bruges bagud (overlevelses-bias). Research, ikke rådgivning.")
    with open("output/robustness_backtest.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()

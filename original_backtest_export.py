"""
original_backtest_export.py — kører den ORIGINALE swing-strategi (backtest.py:
3-måneders rebalancering, maks 5 positioner, trend+momentum, −15 % stop) og
gemmer en sammenligning med ACWI i output/original_backtest.md/.json.

To perioder:
  - Hele historikken fra Config.start_date (2015)
  - Samme periode som intradag-/kort swing-backtesten (fra 2023-11-21),
    så resultaterne kan sammenlignes direkte.

Ingen ændring af strategien — kun en eksport, så Claude kan læse resultatet.
Research-værktøj, ikke finansiel rådgivning.
"""

import datetime as dt
import json
import os

import pandas as pd

from config import Config
from universe import BENCHMARK
from data_fetch import load_prices
from backtest import run_backtest
from metrics import summary

COMPARE_FROM = "2023-11-21"


def yearly(equity: pd.Series) -> dict:
    y = equity.groupby(equity.index.year).last()
    first = equity.iloc[0]
    prev = pd.concat([pd.Series([first], index=[y.index[0] - 1]), y]).shift(1).iloc[1:]
    return {int(k): round(float(v) * 100, 1) for k, v in (y / prev - 1).items()}


def run(prices: pd.DataFrame, cfg: Config, label: str) -> dict:
    res = run_backtest(prices, cfg)
    s = summary(res["equity_curve"], res["benchmark_curve"], res["trade_log"])
    eq, bm = res["equity_curve"], res["benchmark_curve"]
    return {
        "label": label,
        "fra": str(eq.index[0].date()), "til": str(eq.index[-1].date()),
        "slutvaerdi_dkk": round(float(eq.iloc[-1])),
        "samlet_pct": round(s["total_return"] * 100, 1),
        "acwi_samlet_pct": round(s["bench_total_return"] * 100, 1),
        "cagr_pct": round(s["cagr"] * 100, 1),
        "acwi_cagr_pct": round(s["bench_cagr"] * 100, 1),
        "max_dd_pct": round(s["max_drawdown"] * 100, 1),
        "acwi_max_dd_pct": round(s["bench_max_drawdown"] * 100, 1),
        "sharpe": round(s["sharpe"], 2), "acwi_sharpe": round(s["bench_sharpe"], 2),
        "handler": s["trade_n_trades"],
        "hitrate_pct": round(s["trade_win_rate"] * 100, 1),
        "gns_holdetid_dage": round(float(s["trade_avg_holding_days"]), 0),
        "gns_pr_dag_dkk": round(float((eq.iloc[-1] - eq.iloc[0]) / len(eq)), 1),
        "pr_aar_pct": yearly(eq), "acwi_pr_aar_pct": yearly(bm),
        "aktuelle_positioner": sorted(res["final_positions"].keys()),
    }


def main():
    cfg = Config()
    prices = load_prices(cfg, force=True)
    full = run(prices, cfg, f"Hele perioden (fra {cfg.start_date})")

    needed = max(cfg.trend_sma_window, cfg.momentum_lookback_days) + 1
    start_pos = int(prices.index.searchsorted(pd.Timestamp(COMPARE_FROM)))
    recent = run(prices.iloc[max(0, start_pos - needed):], cfg, f"Samme periode som de nye tests (fra {COMPARE_FROM})")

    out = {"genereret_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "perioder": [full, recent]}
    os.makedirs("output", exist_ok=True)
    with open("output/original_backtest.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)

    md = [f"# Original swing-strategi vs. ACWI — {out['genereret_utc']}", ""]
    for p in out["perioder"]:
        md += [f"## {p['label']}: {p['fra']} → {p['til']}", "",
               "| | Strategi | ACWI |", "|---|---|---|",
               f"| Samlet afkast | {p['samlet_pct']}% | {p['acwi_samlet_pct']}% |",
               f"| Årligt (CAGR) | {p['cagr_pct']}% | {p['acwi_cagr_pct']}% |",
               f"| Max drawdown | {p['max_dd_pct']}% | {p['acwi_max_dd_pct']}% |",
               f"| Sharpe | {p['sharpe']} | {p['acwi_sharpe']} |",
               "", f"Handler: {p['handler']} · hitrate {p['hitrate_pct']}% · gns. holdetid {p['gns_holdetid_dage']} dage", "",
               "| År | Strategi | ACWI |", "|---|---|---|"]
        md += [f"| {y} | {p['pr_aar_pct'][y]}% | {p['acwi_pr_aar_pct'].get(y, '–')}% |" for y in p["pr_aar_pct"]]
        md.append("")
    md.append("Forbehold: dagens univers bruges bagud (overlevelses-bias). Research, ikke rådgivning.")
    with open("output/original_backtest.md", "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()

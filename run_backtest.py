#!/usr/bin/env python3
"""
Hovedscript: hent data -> kør backtest -> generér rapport.

Kør:
    python run_backtest.py            # brug cache hvis den findes
    python run_backtest.py --force    # tving frisk datahentning

Kræver almindelig internetadgang (yfinance/Yahoo Finance). Output lander i
output/report.html.
"""

import os
import sys

from config import Config
from universe import full_universe, BENCHMARK
from data_fetch import load_prices
from backtest import run_backtest
from report import generate_report


def main():
    force = "--force" in sys.argv
    cfg = Config()

    print("=== Swing trading-agent: backtest ===")
    print(f"Periode: {cfg.start_date} -> {cfg.end_date or 'i dag'}")
    print(f"Maks positioner: {cfg.max_positions} | Rebalancering: hver {cfg.rebalance_every_days} handelsdage")
    print()

    prices = load_prices(cfg, force=force)

    universe_size = len(full_universe()) + 1  # + benchmark
    missing = sorted(set(full_universe() + [BENCHMARK]) - set(prices.columns))

    print("\nKører backtest...")
    result = run_backtest(prices, cfg)

    out_path = os.path.join("output", "report.html")
    out_path, summ = generate_report(cfg, prices, result, missing, universe_size, out_path)

    print("\n=== Resultat ===")
    print(f"Samlet afkast:  {summ['total_return']*100:+.1f}%  (benchmark: {summ['bench_total_return']*100:+.1f}%)")
    print(f"CAGR:           {summ['cagr']*100:+.1f}%  (benchmark: {summ['bench_cagr']*100:+.1f}%)")
    print(f"Max drawdown:   {summ['max_drawdown']*100:.1f}%  (benchmark: {summ['bench_max_drawdown']*100:.1f}%)")
    print(f"Sharpe:         {summ['sharpe']:.2f}  (benchmark: {summ['bench_sharpe']:.2f})")
    print(f"Antal handler:  {summ['trade_n_trades']}  |  Andel vindere: {summ['trade_win_rate']*100:.0f}%")
    print(f"\nRapport gemt: {out_path}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Kør kandidat-screeneren: TA + analytiker + sentiment-rangering.

Kør:
    python run_screener.py

Output: output/screener.html (åbn i browser, eller vis inline i Colab —
se README.md).

Kræver almindelig internetadgang (Yahoo Finance, evt. StockTwits/Reddit).
Bruger samme kurs-cache som run_backtest.py.
"""

from config import Config
from universe import full_universe
from screener import run_screener
from screener_report import generate_screener_report


def main():
    cfg = Config()
    print("=== Swing trading-agent: kandidat-screener ===")
    print(f"Shortlist: top {cfg.screener_shortlist_size} (TA) -> endelig top {cfg.screener_top_n}")
    print(f"Vægtning: {cfg.screener_ta_weight*100:.0f}% teknisk / {cfg.screener_sentiment_weight*100:.0f}% analytiker+sentiment\n")

    candidates = run_screener(cfg)
    universe_size = len(full_universe())

    out_path = generate_screener_report(cfg, candidates, universe_size, "output/screener.html")

    print("\n=== Topkandidater ===")
    for rank, c in enumerate(candidates, 1):
        upside = f"{c['upside_pct']:+.1f}%" if c["upside_pct"] is not None else "–"
        print(f"{rank:2d}. {c['ticker']:12s} score={c['composite_score']:5.1f}  "
              f"TA={c['ta']['ta_score']:5.1f}  upside={upside}")

    print(f"\nRapport gemt: {out_path}")
    print("\nHUSK: research-værktøj, ikke finansiel rådgivning.")


if __name__ == "__main__":
    main()

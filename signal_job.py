"""
signal_job.py — Lag 1: genererer signaler til Saxo-eksekveringen.

Køres af sit eget GitHub Actions-workflow
(.github/workflows/signal_job.yml), akkurat som alert_check.py — men
adskilt fra det: alert_check.py sender dig fortsat en almindelig
notifikation ved KØB/SÆLG, helt uafhængigt af dette. Denne fil skriver
IKKE en notifikation — den skriver strukturerede, idempotente signaler
til trading/state/trading.db (se trading/db.py), som eksekveringslaget
(trading/run_execution_cycle.py) senere læser og handler på. De to jobs
kan sagtens køre lige efter hinanden i samme daglige workflow.

Genbruger samme analyse som alert_check.py (core.analyze_ticker,
core.find_top_candidates) — kun "output-enden" er anderledes.

Idempotens: external_reference beregnes ud fra dags-DATOEN, ikke et
fuldt tidsstempel (se decision.make_external_reference) — kører jobbet
to gange samme dag (fx en manuel "Run workflow"-genkørsel), indsættes
signalet kun én gang (db.insert_signal opdager UNIQUE-konflikten og
springer stille over). En ny kalenderdag med samme anbefaling opretter
bevidst et NYT signal — det er OK, fordi Lag 2 alligevel afviser et
KØB-signal for en aktie der allerede er i porteføljen (se decision.py)."""

import datetime

from core import analyze_ticker, find_top_candidates, load_static_watchlist
from trading import db
from trading.decision import make_external_reference


def main():
    tickers = set(load_static_watchlist())
    print(f"Statisk watchlist ({len(tickers)} tickere): {sorted(tickers)}")

    print("Kører Top 10-scanning (kan tage et par minutter)...")
    top_candidates, stats = find_top_candidates(top_n=10)
    print(f"Top 10-scanning færdig: {stats}")
    for c in top_candidates:
        tickers.add(c["ticker"])

    if not tickers:
        print("Ingen tickere at analysere.")
        return

    today_str = datetime.date.today().isoformat()
    now_iso = datetime.datetime.utcnow().isoformat()
    new_signals = 0

    with db.connect() as conn:
        for ticker in sorted(tickers):
            try:
                result = analyze_ticker(ticker)
            except Exception as e:
                print(f"Sprang {ticker} over (uventet fejl: {e})")
                continue
            if not result.get("ok"):
                print(f"Sprang {ticker} over ({result.get('reason')})")
                continue

            action = result.get("recommendation", {}).get("action")
            if action not in ("KØB", "SÆLG"):
                continue

            ta = result["ta"]
            plan = result["plan"]
            ext_ref = make_external_reference(ticker, action, today_str)

            inserted = db.insert_signal(
                conn, external_reference=ext_ref, ticker=ticker, direction=action,
                score=result["composite_score"], last_price=ta["last_price"],
                stop_price=plan.get("stop_price") if action == "KØB" else None,
                created_at=now_iso,
            )
            if inserted:
                new_signals += 1
                print(f"Nyt signal: {action} {ticker} @ {ta['last_price']:.2f} "
                      f"(score {result['composite_score']:.0f}/100)")
            else:
                print(f"{action} {ticker}: signal for i dag findes allerede — sprang over.")

    if new_signals:
        print(f"\n{new_signals} nye signal(er) skrevet til trading/state/trading.db.")
    else:
        print("\nIngen nye signaler i dag.")


if __name__ == "__main__":
    main()

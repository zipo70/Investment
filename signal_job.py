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
import os

import model_a
from trading import db
from trading.decision import make_external_reference

# MODEL A (2026-10-25): signaler skrives KUN på rebalanceringsdage (hver
# 63. handelsdag fra model_a.ANCHOR). KØB for model A's top 5 (i rangorden,
# stop −15 %), SÆLG for alle øvrige aktier i universet — Lag 2
# (trading/decision.py) ignorerer SÆLG for aktier vi ikke ejer. Stop-loss
# mellem rebalanceringer ligger i selve Saxo-ordren.
# Sæt MODEL_A_FORCE_REBALANCE=1 for at tvinge en rebalancering (manuel kørsel).


def main():
    rd = model_a.ranking(force=True)
    reb = rd["rebalance"]
    force = os.environ.get("MODEL_A_FORCE_REBALANCE") == "1"
    print(f"Model A top {model_a.N_POSITIONS}: {rd['top']} · rebalanceringsdag: {reb['is_rebalance_day']} "
          f"· næste: {reb['next']} · tvunget: {force}")
    for r in rd["ranked"][:10]:
        print(f"  #{r['rank']:2d} {r['ticker']:12s} momentum {r['momentum'] * 100:+6.1f}%  kurs {r['last']:.2f}")

    if not (reb["is_rebalance_day"] or force):
        print("Ikke rebalanceringsdag — ingen nye signaler (stop-loss ligger i Saxo-ordren).")
        return

    today_str = datetime.date.today().isoformat()
    base = datetime.datetime.utcnow()
    new_signals, n = 0, 0
    with db.connect() as conn:
        top = set(rd["top"])
        ordered = [(t, "KØB") for t in rd["top"]] + \
                  [(t, "SÆLG") for t in sorted(rd["by_ticker"]) if t not in top]
        for ticker, action in ordered:
            sig = rd["by_ticker"][ticker]
            last = sig["last"]
            created = (base + datetime.timedelta(milliseconds=n)).isoformat()  # bevarer rangorden
            n += 1
            inserted = db.insert_signal(
                conn, external_reference=make_external_reference(ticker, action, today_str),
                ticker=ticker, direction=action, score=round(sig["momentum"] * 100, 1),
                last_price=last,
                stop_price=last * (1 - model_a.STOP_PCT) if action == "KØB" else None,
                created_at=created,
            )
            if inserted:
                new_signals += 1
                if action == "KØB":
                    print(f"Nyt signal: KØB {ticker} @ {last:.2f}, stop {last * (1 - model_a.STOP_PCT):.2f}")
    print(f"\n{new_signals} nye signal(er) skrevet (KØB: {len(rd['top'])}, resten SÆLG-rotation).")


if __name__ == "__main__":
    main()

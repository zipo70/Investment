"""
trading/ — Automatisk ordreeksekvering mod Saxo Bank OpenAPI (KUN SIM,
gateway.saxobank.com/sim/openapi — ingen live-handel).

Se SAXO_INTEGRATION.md i repo-roden for arkitektur, hosting-anbefaling og
opsætning. Kort fortalt, tre lag der IKKE kalder hinanden direkte:

  1. Signal   — signal_job.py (repo-rod) + core.py. Skriver signaler.
  2. Beslutning — trading/decision.py. REN funktion, ingen netværk.
  3. Eksekvering — resten af denne pakke (oauth, instrument_mapping,
     order_executor, monitor, guardrails, run_execution_cycle).

Lagene "taler" kun sammen via trading/state/trading.db (se db.py).
"""

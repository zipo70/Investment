"""
trading/run_execution_cycle.py — Lag 3's hovedindgang.

Køres enten:
  - periodisk via GitHub Actions (polling-tilstand — se
    .github/workflows/trading_execution.yml). Anbefalet startpunkt, men
    læs advarslen i SAXO_INTEGRATION.md om OAuth-token-persistens FØR du
    slår SAXO_DRY_RUN fra i den opsætning.
  - i en løkke på en vedvarende host (fx en lille VPS), eventuelt sammen
    med monitor.run_websocket_forever() for ægte realtids-overvågning —
    se SAXO_INTEGRATION.md, "Hosting", for hvornår det giver mening at
    opgradere hertil.

Rækkefølge, hver kørsel:
  1. Stop straks hvis kill-switchen allerede er aktiv (billigt tjek FØR
     vi overhovedet bruger tid på Saxo-kald).
  2. Hent et gyldigt access-token (oauth.get_valid_access_token() —
     stopper agenten selv + notificerer ved fejl, se oauth.py).
  3. Synkronisér lokale ordrer/positioner fra Saxo (monitor.poll_once).
  4. Hent alle 'pending' signaler (db.get_pending_signals) — skrevet af
     Lag 1 (signal_job.py).
  5. For hvert signal: byg en Portfolio ud fra de friskt synkroniserede
     positioner, kald decision.decide() (REN, ingen netværk), og
     eksekvér et evt. resultat via order_executor.execute_order() (som
     selv håndhæver guardrails og sender notifikationer)."""

import datetime
import os

from config import Config
from trading import db, guardrails, monitor, oauth, saxo_api
from trading.decision import Portfolio, PortfolioPosition, Signal, decide
from trading.instrument_mapping import MappingError, lookup_uic
from trading.order_executor import ExecutionError, execute_order
from notify import notify_order_activity


def _report(msg: str):
    """Print — og i GitHub Actions også som annotation, så forløbet kan
    læses via API uden adgang til den fulde log."""
    print(msg)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        safe = msg.replace("%", "%25").replace("\r", "").replace("\n", "%0A")
        print(f"::notice title=Saxo-cyklus::{safe[:3000]}")


def _strategy_cap() -> float:
    """Hvor meget af SIM-kontoen strategien må bruge (kontoens valuta).
    Default = backtestens startkapital, så positionsstørrelserne matcher."""
    raw = os.environ.get("SAXO_STRATEGY_CAPITAL", "").strip()
    return float(raw) if raw else Config().starting_capital


def split_fresh_signals(rows, now, max_age_hours: float):
    """Deler ventende signaler i (friske, forældede). Kun friske må handles —
    et signal er beregnet på dagens kurs og stop-niveau, og bliver
    meningsløst efter et par dage. Friske sorteres med SÆLG først (frigør
    plads) og derefter højeste score, så de bedste køb vælges når der kun
    er plads til få. Netværksfri."""
    fresh, stale = [], []
    for r in rows:
        try:
            created = datetime.datetime.fromisoformat(str(r["created_at"]).replace("Z", ""))
        except ValueError:
            stale.append(r)
            continue
        age_h = (now - created).total_seconds() / 3600.0
        (fresh if age_h <= max_age_hours else stale).append(r)
    fresh.sort(key=lambda r: (0 if r["direction"] == "SÆLG" else 1, -(r["score"] or 0)))
    return fresh, stale


def build_portfolio(positions_rows: dict, capital: dict) -> Portfolio:
    """Portfolio ud fra synkroniserede positioner + Saxo-saldo (allerede
    begrænset af strategy_capital). Netværksfri."""
    positions = {
        ticker: PortfolioPosition(
            ticker=ticker, uic=row.get("uic"),
            entry_price=row["entry_price"], shares=row["shares"],
        )
        for ticker, row in positions_rows.items()
    }
    return Portfolio(cash=capital["cash"], total_value=capital["total_value"], positions=positions)


def run_once():
    if guardrails.kill_switch_active():
        _report("Kill switch er aktiv — afbryder uden at foretage mig noget.")
        return

    access_token = oauth.get_valid_access_token()  # rejser + stopper selv ved fejl
    gcfg = guardrails.GuardrailConfig.from_env()
    cfg = Config()

    try:
        account = saxo_api.get_account(access_token)
        balance = saxo_api.get_balance(access_token, account)
    except saxo_api.SaxoApiError as e:
        notify_order_activity("Kunne ikke hente Saxo-konto/saldo", str(e))
        raise
    capital = saxo_api.strategy_capital(balance, _strategy_cap())
    _report(f"Konto {account['account_key']} ({account['currency']}): "
          f"saldo {balance['total_value']:.0f}, strategi-kapital {capital['total_value']:.0f}, "
          f"ledig {capital['cash']:.0f}. Dry-run: {gcfg.dry_run}")

    with db.connect() as conn:
        try:
            result = monitor.poll_once(conn, access_token)
            _report(f"Synkroniseret {result['synced']} positioner.")
        except Exception as e:
            notify_order_activity("Kunne ikke synkronisere Saxo-konto", str(e))
            _report(f"Advarsel: poll_once fejlede ({e}) — fortsætter med sidst kendte tilstand.")

        max_age = float(os.environ.get("SAXO_SIGNAL_MAX_AGE_HOURS", "36") or 36)
        pending, stale = split_fresh_signals(
            db.get_pending_signals(conn), datetime.datetime.utcnow(), max_age)
        for r in stale:
            db.mark_signal(conn, r["external_reference"], "rejected",
                           reject_reason=f"Forældet (ældre end {max_age:.0f} timer) — ikke handlet")
        if stale:
            _report(f"{len(stale)} forældede signaler afvist (ældre end {max_age:.0f} timer).")
        if not pending:
            _report("Ingen friske ventende signaler.")
            return

        positions_rows = db.load_positions(conn)

        for row in pending:
            signal = Signal(
                ticker=row["ticker"], direction=row["direction"], score=row["score"],
                last_price=row["last_price"], stop_price=row["stop_price"],
                external_reference=row["external_reference"],
            )
            portfolio = build_portfolio(positions_rows, capital)
            proposal = decide(signal, portfolio, cfg)

            if proposal is None:
                db.mark_signal(
                    conn, signal.external_reference, "rejected",
                    reject_reason=("Afvist af beslutningslaget (fx portefølje fuld, "
                                    "aktien allerede holdes, eller intet at sælge)"),
                )
                continue

            try:
                mapping = lookup_uic(access_token, signal.ticker, conn)
            except MappingError as e:
                db.mark_signal(conn, signal.external_reference, "failed", reject_reason=str(e))
                notify_order_activity(f"Instrument-opslag fejlede — {signal.ticker}", str(e))
                continue

            try:
                execute_order(
                    conn, access_token, proposal,
                    uic=mapping["uic"], asset_type=mapping["asset_type"],
                    account=account, last_price=signal.last_price, gcfg=gcfg,
                )
            except (guardrails.GuardrailViolation, ExecutionError, saxo_api.SaxoApiError) as e:
                _report(f"{signal.ticker}: {e}")
                continue  # logget + notificeret inde i execute_order()

            # Køb binder kapital — træk fra, så næste signal i samme kørsel
            # ikke regner med penge der allerede er brugt.
            if proposal.direction == "KØB" and proposal.notional:
                capital["cash"] = max(0.0, capital["cash"] - proposal.notional)
            positions_rows = db.load_positions(conn)

        counts = conn.execute(
            "SELECT status, COUNT(*) n FROM signals GROUP BY status").fetchall()
        _report("Signal-status efter kørsel: " + ", ".join(f"{r['status']}={r['n']}" for r in counts))


if __name__ == "__main__":
    run_once()

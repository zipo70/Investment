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

from config import Config
from trading import db, guardrails, monitor, oauth
from trading.decision import Portfolio, PortfolioPosition, Signal, decide
from trading.instrument_mapping import MappingError, lookup_uic
from trading.order_executor import ExecutionError, execute_order
from notify import notify_order_activity


def _portfolio_from_positions(positions_rows: dict) -> Portfolio:
    positions = {
        ticker: PortfolioPosition(
            ticker=ticker, uic=row.get("uic"),
            entry_price=row["entry_price"], shares=row["shares"],
        )
        for ticker, row in positions_rows.items()
    }
    # TODO: total_value BØR være den faktiske kontoværdi fra Saxo
    # (GET {API_BASE_URL}/port/v1/balances/me), ikke en tilnærmelse.
    # Indtil den er koblet på, bruges cfg.starting_capital som et
    # eksplicit-synligt (og eksplicit forkert) stedfortræder, så det
    # ikke bliver glemt — sæt IKKE SAXO_DRY_RUN=false før dette er rettet.
    total_value = Config().starting_capital
    invested = sum(p.entry_price * p.shares for p in positions.values())
    cash = total_value - invested
    return Portfolio(cash=cash, total_value=total_value, positions=positions)


def run_once():
    if guardrails.kill_switch_active():
        print("Kill switch er aktiv — afbryder uden at foretage mig noget.")
        return

    access_token = oauth.get_valid_access_token()  # rejser + stopper selv ved fejl
    gcfg = guardrails.GuardrailConfig.from_env()
    cfg = Config()

    with db.connect() as conn:
        try:
            monitor.poll_once(conn, access_token)
        except Exception as e:
            notify_order_activity("Kunne ikke synkronisere Saxo-konto", str(e))
            print(f"Advarsel: poll_once fejlede ({e}) — fortsætter med sidst kendte tilstand.")

        pending = db.get_pending_signals(conn)
        if not pending:
            print("Ingen ventende signaler.")
            return

        positions_rows = db.load_positions(conn)

        for row in pending:
            signal = Signal(
                ticker=row["ticker"], direction=row["direction"], score=row["score"],
                last_price=row["last_price"], stop_price=row["stop_price"],
                external_reference=row["external_reference"],
            )
            portfolio = _portfolio_from_positions(positions_rows)
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
                    uic=mapping["uic"], asset_type=mapping["asset_type"], gcfg=gcfg,
                )
            except (guardrails.GuardrailViolation, ExecutionError):
                continue  # allerede logget + notificeret inde i execute_order()

            # Opdatér den lokale positions-visning så EVENTUELLE
            # efterfølgende signaler i samme kørsel (usandsynligt, men
            # billigt at være korrekt om) ser den nye position.
            positions_rows = db.load_positions(conn)


if __name__ == "__main__":
    run_once()

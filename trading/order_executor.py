"""
trading/order_executor.py — Lag 3: sender ét OrderProposal (fra
decision.py) til Saxo som en faktisk ordre, med et vedhæftet stop-loss i
SAMME kald (Saxo understøtter relaterede ordrer via feltet "Orders" i
selve POST'et til /trade/v2/orders — entry + stop afgives samlet,
fremfor to separate kald hvor en fejl mellem dem kunne efterlade en
ubeskyttet position).

ALT her respekterer guardrails.check_guardrails() FØR noget sendes, og
bruger signal.external_reference som Saxo's ExternalReference — kald
ALDRIG Saxo's ordre-endpoints uden om denne funktion."""

import json
import os

import requests

from trading import db, guardrails
from trading.decision import OrderProposal
from notify import notify_order_activity

API_BASE_URL = "https://gateway.saxobank.com/sim/openapi"

# UDFYLD: din Saxo SIM-kontos AccountKey (findes via GET /port/v1/accounts/me
# efter første login) — sæt som miljøvariabel, ikke hardkodet.
ACCOUNT_KEY = os.environ.get("SAXO_ACCOUNT_KEY", "UDFYLD_KONTO_KEY")


class ExecutionError(Exception):
    pass


def execute_order(conn, access_token: str, proposal: OrderProposal,
                   uic: int, asset_type: str, gcfg: guardrails.GuardrailConfig) -> dict:
    """Eksekverer ét ordreforslag. Returnerer en dict med status.
    Rejser GuardrailViolation eller ExecutionError ved afvisning/fejl —
    begge tilfælde er allerede logget i trading.db og notificeret inden
    de rejses, så kald-stedet normalt bare kan fange og fortsætte til
    næste signal (se run_execution_cycle.py)."""

    # 1) Idempotens FØRST — er denne reference allerede afgivet før?
    existing = conn.execute(
        "SELECT * FROM orders WHERE external_reference = ?",
        (proposal.external_reference,),
    ).fetchone()
    if existing:
        notify_order_activity(
            f"Sprang over (allerede afgivet) — {proposal.ticker}",
            f"{proposal.external_reference} findes allerede i orders-tabellen "
            f"med status '{existing['status']}'. Ingen ny ordre sendt.",
        )
        return dict(existing)

    order_amount = proposal.notional  # None ved SÆLG — check_guardrails håndterer det

    # 2) Guardrails FØR noget som helst sendes til Saxo.
    try:
        guardrails.check_guardrails(conn, order_amount, gcfg)
    except guardrails.GuardrailViolation as e:
        db.mark_signal(conn, proposal.external_reference, "rejected", reject_reason=str(e))
        db.log_activity(conn, "guardrail_violation", f"{proposal.ticker}: {e}")
        notify_order_activity(f"Ordre AFVIST af sikkerhedsspærre — {proposal.ticker}", str(e))
        raise

    body = _build_order_body(proposal, uic, asset_type)

    # 3) Dry-run (standard): log som om, men send INTET til Saxo.
    if gcfg.dry_run:
        db.insert_order(
            conn, external_reference=proposal.external_reference,
            saxo_order_id=None, uic=uic, asset_type=asset_type,
            direction=proposal.direction, notional=proposal.notional,
            shares=proposal.shares_to_sell, stop_price=proposal.stop_price,
            status="submitted",
            raw_response_json=json.dumps({"dry_run": True, "body": body}),
        )
        db.mark_signal(conn, proposal.external_reference, "ordered")
        db.log_activity(conn, "dry_run_order", f"{proposal.ticker}: {proposal.reason}")
        notify_order_activity(
            f"DRY-RUN — ville have sendt ordre: {proposal.ticker}",
            f"{proposal.direction} — {proposal.reason}\n"
            "(Intet sendt til Saxo — SAXO_DRY_RUN=true. Sæt til 'false' når du "
            "er klar til at teste rigtige SIM-ordrer.)",
        )
        return {"external_reference": proposal.external_reference, "status": "submitted", "dry_run": True}

    # 4) Precheck — Saxo tilbyder /trade/v2/orders/precheck, som fanger
    #    fx utilstrækkelig købekraft FØR den rigtige ordre sendes.
    precheck = requests.post(
        f"{API_BASE_URL}/trade/v2/orders/precheck",
        headers={"Authorization": f"Bearer {access_token}"},
        json=body, timeout=20,
    )
    if precheck.status_code >= 300:
        db.mark_signal(conn, proposal.external_reference, "failed", reject_reason=precheck.text)
        notify_order_activity(f"Precheck fejlede — {proposal.ticker}", precheck.text)
        raise ExecutionError(f"Precheck afvist (HTTP {precheck.status_code}): {precheck.text}")

    # 5) Selve ordren (entry + stop i ét kald, se _build_order_body).
    resp = requests.post(
        f"{API_BASE_URL}/trade/v2/orders",
        headers={"Authorization": f"Bearer {access_token}"},
        json=body, timeout=20,
    )
    if resp.status_code >= 300:
        db.insert_order(
            conn, external_reference=proposal.external_reference,
            saxo_order_id=None, uic=uic, asset_type=asset_type,
            direction=proposal.direction, notional=proposal.notional,
            shares=proposal.shares_to_sell, stop_price=proposal.stop_price,
            status="error", raw_response_json=resp.text,
        )
        db.mark_signal(conn, proposal.external_reference, "failed", reject_reason=resp.text)
        notify_order_activity(f"ORDRE FEJLEDE — {proposal.ticker}", resp.text)
        raise ExecutionError(f"Ordre afvist (HTTP {resp.status_code}): {resp.text}")

    data = resp.json()
    saxo_order_id = data.get("OrderId")
    db.insert_order(
        conn, external_reference=proposal.external_reference,
        saxo_order_id=saxo_order_id, uic=uic, asset_type=asset_type,
        direction=proposal.direction, notional=proposal.notional,
        shares=proposal.shares_to_sell, stop_price=proposal.stop_price,
        status="submitted", raw_response_json=resp.text,
    )
    db.mark_signal(conn, proposal.external_reference, "ordered")
    notify_order_activity(
        f"ORDRE SENDT — {proposal.ticker}",
        f"{proposal.direction} — Saxo-ordre-id {saxo_order_id}\n{proposal.reason}",
    )
    return {"external_reference": proposal.external_reference, "saxo_order_id": saxo_order_id, "status": "submitted"}


def _build_order_body(proposal: OrderProposal, uic: int, asset_type: str) -> dict:
    """TODO: dette er en SKITSE af Saxo-ordre-JSON'en, ikke verificeret
    mod den faktiske API-kontrakt — kør altid mod precheck-endpointet
    først og sammenlign med Saxos egen OpenAPI-dokumentation/Swagger for
    /trade/v2/orders, før du slår SAXO_DRY_RUN fra.

    Nøglepunkter forslaget hviler på:
      - ExternalReference sikrer idempotens hos Saxo selv (ikke kun i
        vores egen database).
      - "Orders"-listen er en relateret ordre (her: stop-loss), afgivet i
        SAMME kald som entry-ordren, jf. briefens krav om at de to aldrig
        må kunne komme ud af trit med hinanden."""
    if proposal.direction == "KØB":
        return {
            "AccountKey": ACCOUNT_KEY,
            "Uic": uic,
            "AssetType": asset_type,
            "BuySell": "Buy",
            "OrderType": "Market",
            # TODO: Saxo forventer typisk ANTAL AKTIER i "Amount", ikke et
            # beløb — omregn proposal.notional til antal via seneste kurs
            # (fra core.py's ta["last_price"], evt. hentet på ny lige
            # inden ordreafgivelse) før dette kald går live.
            "Amount": proposal.notional,
            "OrderDuration": {"DurationType": "DayOrder"},
            "ExternalReference": proposal.external_reference,
            "Orders": [
                {
                    "Uic": uic,
                    "AssetType": asset_type,
                    "BuySell": "Sell",
                    "OrderType": "Stop",
                    "OrderPrice": proposal.stop_price,
                    "OrderDuration": {"DurationType": "GoodTillCancel"},
                    "ExternalReference": proposal.external_reference + "-stop",
                }
            ],
        }
    else:  # SÆLG
        return {
            "AccountKey": ACCOUNT_KEY,
            "Uic": uic,
            "AssetType": asset_type,
            "BuySell": "Sell",
            "OrderType": "Market",
            "Amount": proposal.shares_to_sell,
            "OrderDuration": {"DurationType": "DayOrder"},
            "ExternalReference": proposal.external_reference,
        }

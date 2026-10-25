"""
trading/order_executor.py — Lag 3: sender ét OrderProposal (fra
decision.py) til Saxo som en faktisk ordre.

KØB: markedsordre + stop-loss som relateret ordre i SAMME kald, så en fejl
mellem to kald aldrig kan efterlade en ubeskyttet position.
SÆLG: annullerer først aktiens åbne stop-ordre(r), så en gammel stop ikke
kan udløses bagefter, og sender derefter en markedsordre.

Rækkefølge (alt respekterer guardrails FØR noget sendes):
  1. Idempotens: er referencen allerede afgivet? -> spring over.
  2. Guardrails (beløbsloft, dagsloft, kill switch).
  3. Beregn antal aktier: beløb i kontovaluta / (kurs × valutakurs).
     Valutakursen hentes via et precheck på 1 aktie
     (InstrumentToAccountConversionRate) — ingen separat valuta-kilde.
  4. Dry-run (standard): log som om, send intet.
  5. Precheck af den rigtige ordre -> afgiv ordren.

Kald ALDRIG Saxos ordre-endpoints uden om denne funktion."""

import json
import math

from trading import db, guardrails, saxo_api
from trading.decision import OrderProposal
from notify import notify_order_activity


class ExecutionError(Exception):
    pass


def compute_share_amount(notional: float, last_price: float, conversion_rate: float) -> int:
    """Antal hele aktier for et beløb i kontovaluta. Runder NED, så
    ordren aldrig overstiger beløbet (og dermed guardrail-loftet).
    Netværksfri."""
    if not notional or not last_price or not conversion_rate:
        return 0
    price_in_account_ccy = last_price * conversion_rate
    if price_in_account_ccy <= 0:
        return 0
    return int(math.floor(notional / price_in_account_ccy + 1e-9))


def build_buy_body(*, account_key: str, uic: int, asset_type: str, shares: int,
                   stop_price: float, stop_order_type: str, external_reference: str) -> dict:
    return {
        "AccountKey": account_key,
        "Uic": uic,
        "AssetType": asset_type,
        "BuySell": "Buy",
        "OrderType": "Market",
        "Amount": shares,
        "ManualOrder": False,
        "OrderDuration": {"DurationType": "DayOrder"},
        "ExternalReference": external_reference,
        "Orders": [
            {
                "Uic": uic,
                "AssetType": asset_type,
                "BuySell": "Sell",
                "OrderType": stop_order_type,
                "OrderPrice": stop_price,
                "Amount": shares,
                "ManualOrder": False,
                "OrderDuration": {"DurationType": "GoodTillCancel"},
                "ExternalReference": external_reference + "-stop",
            }
        ],
    }


def build_sell_body(*, account_key: str, uic: int, asset_type: str, shares: float,
                    external_reference: str) -> dict:
    return {
        "AccountKey": account_key,
        "Uic": uic,
        "AssetType": asset_type,
        "BuySell": "Sell",
        "OrderType": "Market",
        "Amount": shares,
        "ManualOrder": False,
        "OrderDuration": {"DurationType": "DayOrder"},
        "ExternalReference": external_reference,
    }


def find_stop_orders_for_uic(open_orders: list, uic: int) -> list:
    """Åbne sælg-stop-ordrer på instrumentet (dem der skal væk før et salg).
    Netværksfri."""
    return [
        o["OrderId"] for o in open_orders
        if o.get("Uic") == uic and o.get("BuySell") == "Sell"
        and str(o.get("OpenOrderType", o.get("OrderType", ""))).startswith("Stop")
        and o.get("OrderId")
    ]


def _fail(conn, proposal, reason, title):
    db.mark_signal(conn, proposal.external_reference, "failed", reject_reason=reason)
    db.log_activity(conn, "order_failed", f"{proposal.ticker}: {reason}")
    notify_order_activity(f"{title} — {proposal.ticker}", reason)
    raise ExecutionError(reason)


def execute_order(conn, access_token: str, proposal: OrderProposal, *,
                  uic: int, asset_type: str, account: dict,
                  last_price: float, gcfg: guardrails.GuardrailConfig) -> dict:
    """Eksekverer ét ordreforslag. Rejser GuardrailViolation eller
    ExecutionError ved afvisning/fejl — begge er logget i trading.db og
    notificeret inden de rejses, så kaldet blot kan fange og fortsætte."""

    # 1) Idempotens
    existing = conn.execute(
        "SELECT * FROM orders WHERE external_reference = ?",
        (proposal.external_reference,),
    ).fetchone()
    if existing:
        notify_order_activity(
            f"Sprang over (allerede afgivet) — {proposal.ticker}",
            f"{proposal.external_reference} findes allerede med status '{existing['status']}'.",
        )
        return dict(existing)

    # 2) Guardrails
    try:
        guardrails.check_guardrails(conn, proposal.notional, gcfg)
    except guardrails.GuardrailViolation as e:
        db.mark_signal(conn, proposal.external_reference, "rejected", reject_reason=str(e))
        db.log_activity(conn, "guardrail_violation", f"{proposal.ticker}: {e}")
        notify_order_activity(f"Ordre AFVIST af sikkerhedsspærre — {proposal.ticker}", str(e))
        raise

    account_key = account["account_key"]

    # 3) Byg ordren
    if proposal.direction == "KØB":
        probe = saxo_api.precheck(access_token, {
            "AccountKey": account_key, "Uic": uic, "AssetType": asset_type,
            "BuySell": "Buy", "OrderType": "Market", "Amount": 1, "ManualOrder": False,
            "OrderDuration": {"DurationType": "DayOrder"},
        })
        rate = probe["conversion_rate"] or (1.0 if probe["ok"] else None)
        if rate is None:
            _fail(conn, proposal, f"Kunne ikke hente valutakurs: {probe['error']}", "Precheck fejlede")
        shares = compute_share_amount(proposal.notional, last_price, rate)
        if shares < 1:
            _fail(conn, proposal,
                  f"Beløbet ({proposal.notional:.0f}) rækker ikke til én aktie "
                  f"(kurs {last_price:.2f} × valutakurs {rate:.4f}).", "Ordre ikke mulig")
        details = saxo_api.get_instrument_details(access_token, uic, asset_type)
        stop_type = saxo_api.choose_stop_order_type(details)
        stop_price = saxo_api.round_to_tick(proposal.stop_price, details, direction="down")
        body = build_buy_body(
            account_key=account_key, uic=uic, asset_type=asset_type, shares=shares,
            stop_price=stop_price, stop_order_type=stop_type,
            external_reference=proposal.external_reference,
        )
    else:
        shares = proposal.shares_to_sell
        stop_price = None
        body = build_sell_body(
            account_key=account_key, uic=uic, asset_type=asset_type, shares=shares,
            external_reference=proposal.external_reference,
        )

    # 4) Dry-run (standard): log, send intet
    if gcfg.dry_run:
        db.insert_order(
            conn, external_reference=proposal.external_reference, saxo_order_id=None,
            uic=uic, asset_type=asset_type, direction=proposal.direction,
            notional=proposal.notional, shares=shares, stop_price=stop_price,
            status="submitted",  # tabellens CHECK kender ikke 'dry_run' — markeret i raw_response_json
            raw_response_json=json.dumps({"dry_run": True, "body": body}),
        )
        db.mark_signal(conn, proposal.external_reference, "ordered")
        db.log_activity(conn, "dry_run_order", f"{proposal.ticker}: {proposal.reason}")
        notify_order_activity(
            f"DRY-RUN — ville have sendt ordre: {proposal.ticker}",
            f"{proposal.direction} {shares} stk. — {proposal.reason}\n"
            "(Intet sendt til Saxo — SAXO_DRY_RUN=true.)",
        )
        return {"external_reference": proposal.external_reference, "status": "dry_run",
                "shares": shares, "body": body}

    # 5a) Ved salg: fjern åbne stop-ordrer først
    if proposal.direction == "SÆLG":
        try:
            stops = find_stop_orders_for_uic(saxo_api.list_open_orders(access_token), uic)
            saxo_api.cancel_orders(access_token, account_key, stops)
            if stops:
                db.log_activity(conn, "stop_cancelled", f"{proposal.ticker}: {stops}")
        except saxo_api.SaxoApiError as e:
            _fail(conn, proposal, f"Kunne ikke annullere stop-ordre før salg: {e}", "Salg afbrudt")

    # 5b) Precheck af den rigtige ordre
    check = saxo_api.precheck(access_token, body)
    if not check["ok"]:
        _fail(conn, proposal, f"Precheck afvist: {check['error']}", "Precheck fejlede")

    # 5c) Afgiv ordren
    try:
        data = saxo_api.place_order(access_token, body)
    except saxo_api.SaxoApiError as e:
        db.insert_order(
            conn, external_reference=proposal.external_reference, saxo_order_id=None,
            uic=uic, asset_type=asset_type, direction=proposal.direction,
            notional=proposal.notional, shares=shares, stop_price=stop_price,
            status="error", raw_response_json=str(e),
        )
        _fail(conn, proposal, str(e), "ORDRE FEJLEDE")

    saxo_order_id = data.get("OrderId")
    db.insert_order(
        conn, external_reference=proposal.external_reference, saxo_order_id=saxo_order_id,
        uic=uic, asset_type=asset_type, direction=proposal.direction,
        notional=proposal.notional, shares=shares, stop_price=stop_price,
        status="submitted", raw_response_json=json.dumps(data),
    )
    db.mark_signal(conn, proposal.external_reference, "ordered")
    db.log_activity(conn, "order_placed", f"{proposal.ticker}: {proposal.direction} {shares} stk.")
    notify_order_activity(
        f"SIM-ORDRE SENDT — {proposal.ticker}",
        f"{proposal.direction} {shares} stk. — Saxo-ordre-id {saxo_order_id}\n{proposal.reason}",
    )
    return {"external_reference": proposal.external_reference, "saxo_order_id": saxo_order_id,
            "status": "submitted", "shares": shares}

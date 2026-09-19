"""
trading/monitor.py — Lag 3: overvågning af ordrer/positioner hos Saxo.

To driftsformer, samme underliggende hensigt (hold trading.db i sync med
den faktiske konto, så beslutningslaget altid regner på virkeligheden):

  - poll_once(): étgangs REST-forespørgsel efter åbne ordrer/positioner.
    Dette er det der REELT kan køre i den simple GitHub Actions-opsætning
    (som ikke holder en proces kørende mellem workflow-kørsler) — kald
    den i en løkke med et par minutters mellemrum, eller én gang pr.
    scheduled kørsel. Se SAXO_INTEGRATION.md, "Hosting".

  - run_websocket_forever(): ægte, vedvarende Saxo-streaming (WebSocket)
    for øjeblikkelig besked om fills/statusændringer. Kræver en proces
    der faktisk bliver ved med at køre (en lille VPS — IKKE GitHub
    Actions). Skitseret, ikke færdigimplementeret — se docstring
    nedenfor for hvorfor og hvordan du selv færdiggør den, når/hvis du
    får den type hosting."""

import json
import os

import requests

from trading import db

API_BASE_URL = os.environ.get("SAXO_API_BASE_URL", "https://gateway.saxobank.com/sim/openapi")
STREAMING_BASE_URL = os.environ.get(
    "SAXO_STREAMING_URL", "wss://streaming.saxobank.com/sim/openapi/streamingws/connect"
)


def poll_once(conn, access_token: str):
    """Henter åbne ordrer + positioner fra Saxo og synkroniserer den
    lokale positions-tabel (som decision.py's Portfolio bygges ud fra) —
    så en position lukket/åbnet manuelt direkte i Saxo også bliver
    respekteret, ikke kun ordrer VI selv har sendt."""
    headers = {"Authorization": f"Bearer {access_token}"}

    orders_resp = requests.get(f"{API_BASE_URL}/port/v1/orders/me", headers=headers, timeout=20)
    orders_resp.raise_for_status()
    for o in orders_resp.json().get("Data", []):
        ext_ref = o.get("ExternalReference")
        if not ext_ref:
            continue
        db.update_order_status(
            conn, ext_ref, status=_map_saxo_order_status(o.get("Status")),
            raw_response_json=json.dumps(o),
        )

    positions_resp = requests.get(f"{API_BASE_URL}/port/v1/positions/me", headers=headers, timeout=20)
    positions_resp.raise_for_status()

    # TODO: Saxo's position-svar identificerer instrumentet via Uic, ikke
    # via Yahoo-tickeren "Mine Aktier" ellers bruger overalt — byg en
    # baglæns opslagstabel (Uic -> yahoo_ticker) ud fra instrument_cache
    # (se db.py) i stedet for feltet "_YahooTickerHint" herunder, som
    # IKKE er et rigtigt Saxo-felt (det er en placeholder, så skelettets
    # hensigt er tydelig — udfyld selv det rigtige opslag).
    saxo_tickers_seen = set()
    for p in positions_resp.json().get("Data", []):
        base = p.get("PositionBase", {})
        ticker = base.get("_YahooTickerHint")
        if not ticker:
            continue
        saxo_tickers_seen.add(ticker)
        db.upsert_position(
            conn, ticker=ticker, uic=base.get("Uic"), asset_type=base.get("AssetType"),
            shares=base.get("Amount"), entry_price=base.get("OpenPrice"),
            entry_date=base.get("ExecutionTimeOpen"), external_reference=None,
        )

    # Positioner vi troede var åbne, men som Saxo ikke længere viser -> lukket
    # (fx stoppet ud, eller lukket manuelt) -> fjern lokalt.
    for ticker in list(db.load_positions(conn).keys()):
        if ticker not in saxo_tickers_seen:
            db.remove_position(conn, ticker)


def _map_saxo_order_status(saxo_status: str) -> str:
    mapping = {
        "Working": "working", "Filled": "filled",
        "Cancelled": "cancelled", "Rejected": "rejected",
    }
    return mapping.get(saxo_status, "working")


def run_websocket_forever(access_token: str, on_update=None):
    """SKITSE — ikke færdigimplementeret. Kræver `pip install
    websocket-client` og en proces der reelt bliver ved med at køre
    (se modul-docstring og SAXO_INTEGRATION.md, "Hosting"). Grov
    opskrift til når du er klar til at udfylde den:

      1. POST {API_BASE_URL}/streamingws/connect -> et ContextId.
      2. Abonnér med samme ContextId:
         POST {API_BASE_URL}/trade/v1/orders/subscriptions
         POST {API_BASE_URL}/port/v1/positions/subscriptions
      3. Åbn en WebSocket mod
         f"{STREAMING_BASE_URL}?contextId=...&access_token=..."
      4. For hver besked: Saxo bruger et let-vægts binært envelope-
         format (se Saxos "Streaming"-dokumentation for byte-layoutet) —
         parse det, opdatér trading.db akkurat som poll_once() gør, og
         kald evt. on_update(besked) videre til eget brug.

    Indtil du har en vedvarende host: kald poll_once() i en løkke (fx
    hvert minut) fra run_execution_cycle.py i stedet — funktionelt
    identisk resultat i trading.db, blot med op til et minuts forsinkelse
    i stedet for øjeblikkelig besked."""
    raise NotImplementedError(
        "WebSocket-overvågning er skitseret i docstringen ovenfor, men ikke "
        "implementeret — kræver vedvarende hosting (se SAXO_INTEGRATION.md). "
        "Brug poll_once() i en løkke indtil da."
    )

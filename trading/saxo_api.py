"""
trading/saxo_api.py — Lag 3: små, fælles hjælpere til Saxo OpenAPI (SIM).

Samlet ét sted, så ordre-, overvågnings- og kontologikken ikke hver især
gentager URL'er, headers og fejlfortolkning. Alle funktioner tager et
access-token og laver ét eller få HTTP-kald; ren fortolkningslogik
(parse_precheck, aggregate_positions, round_to_tick, choose_stop_order_type)
er netværksfri, så den kan unit-testes (se tests/test_saxo_execution.py).

Fakta tjekket mod Saxos dokumentation 2026-10-25:
  - Amount angives i instrumentets basisenhed, dvs. ANTAL AKTIER for aktier.
  - ManualOrder skal angives (false for automatiseret ordreafgivelse).
  - Relaterede ordrer (fx stop-loss) afgives i "Orders"-listen i samme kald.
  - Annullering: DELETE /trade/v2/orders/{OrderIds}?AccountKey=...
  - Precheck-svaret indeholder ErrorInfo ved fejl og
    InstrumentToAccountConversionRate (valutakurs instrument -> konto).
"""

import math
import os

import requests

API_BASE_URL = os.environ.get("SAXO_API_BASE_URL", "https://gateway.saxobank.com/sim/openapi")
TIMEOUT = 20


class SaxoApiError(Exception):
    pass


def _headers(access_token: str) -> dict:
    return {"Authorization": f"Bearer {access_token}"}


def _get(access_token: str, path: str, params=None) -> dict:
    resp = requests.get(f"{API_BASE_URL}{path}", headers=_headers(access_token),
                        params=params, timeout=TIMEOUT)
    if resp.status_code >= 300:
        raise SaxoApiError(f"GET {path} fejlede (HTTP {resp.status_code}): {resp.text[:500]}")
    return resp.json() if resp.content else {}


# --- Konto -----------------------------------------------------------------

def get_account(access_token: str) -> dict:
    """Returnerer {"account_key", "client_key", "currency"} for kontoen.
    SAXO_ACCOUNT_KEY (miljøvariabel/secret) vinder, hvis den er sat — ellers
    bruges den første aktive konto fra /port/v1/accounts/me, så du ikke
    SKAL slå den op manuelt på telefonen."""
    data = _get(access_token, "/port/v1/accounts/me").get("Data", [])
    if not data:
        raise SaxoApiError("Ingen konti fundet på Saxo-login'et.")
    wanted = os.environ.get("SAXO_ACCOUNT_KEY", "").strip()
    if wanted and not wanted.startswith("UDFYLD"):
        match = [a for a in data if a.get("AccountKey") == wanted]
        if not match:
            raise SaxoApiError(
                f"SAXO_ACCOUNT_KEY er sat, men findes ikke blandt login'ets {len(data)} konti."
            )
        acc = match[0]
    else:
        active = [a for a in data if a.get("Active", True)]
        acc = (active or data)[0]
    return {
        "account_key": acc["AccountKey"],
        "client_key": acc.get("ClientKey"),
        "currency": acc.get("Currency"),
    }


def get_balance(access_token: str, account: dict) -> dict:
    """{"total_value", "cash", "currency"} i kontoens valuta."""
    data = _get(access_token, "/port/v1/balances",
                params={"AccountKey": account["account_key"], "ClientKey": account.get("client_key")})
    total = data.get("TotalValue")
    cash = data.get("CashAvailableForTrading", data.get("CashBalance"))
    if total is None or cash is None:
        raise SaxoApiError(f"Uventet saldo-svar fra Saxo: {str(data)[:300]}")
    return {"total_value": float(total), "cash": float(cash), "currency": data.get("Currency")}


def strategy_capital(balance: dict, cap: float) -> dict:
    """Begrænser den kapital strategien må bruge til `cap` (i kontoens
    valuta). Saxos SIM-konti fødes typisk med et stort fiktivt beløb; uden
    loftet ville 20 %-positioner blive langt større end backtestens og
    blive afvist af max_order_amount-guardrailen. Netværksfri."""
    total = balance["total_value"]
    invested = max(0.0, total - balance["cash"])
    capped_total = min(total, cap) if cap and cap > 0 else total
    capped_cash = max(0.0, min(balance["cash"], capped_total - invested))
    return {"total_value": capped_total, "cash": capped_cash}


# --- Instrumenter ------------------------------------------------------------

def get_instrument_details(access_token: str, uic: int, asset_type: str) -> dict:
    return _get(access_token, f"/ref/v1/instruments/details/{uic}/{asset_type}")


def choose_stop_order_type(details: dict) -> str:
    """Aktier hos Saxo bruger typisk "StopIfTraded"; FX bruger "Stop".
    Vælg ud fra instrumentets egne understøttede ordretyper. Netværksfri."""
    supported = set(details.get("SupportedOrderTypes") or [])
    for candidate in ("StopIfTraded", "Stop", "StopIfBid", "StopIfOffered"):
        if candidate in supported:
            return candidate
    return "StopIfTraded"


def round_to_tick(price: float, details: dict, direction: str = "down") -> float:
    """Afrunder en pris til instrumentets tick-størrelse (Saxo afviser
    ellers ordren). Bruger TickSizeScheme hvis den findes (prisafhængig
    tick), ellers TickSize, ellers 2 decimaler. Netværksfri."""
    tick = None
    scheme = details.get("TickSizeScheme") or {}
    elements = sorted(scheme.get("Elements") or [], key=lambda e: e.get("HighPrice", 0))
    for el in elements:
        if price <= el.get("HighPrice", float("inf")):
            tick = el.get("TickSize")
            break
    if tick is None:
        tick = scheme.get("DefaultTickSize") or details.get("TickSize")
    if not tick:
        return round(price, 2)
    steps = price / tick
    steps = math.floor(steps + 1e-9) if direction == "down" else math.ceil(steps - 1e-9)
    decimals = max(0, -int(math.floor(math.log10(tick)))) if tick < 1 else 0
    return round(steps * tick, decimals + 2)


# --- Ordrer -----------------------------------------------------------------

def parse_precheck(status_code: int, data: dict) -> dict:
    """Fortolker et precheck-svar. Saxo kan melde fejl både via HTTP-status
    og via ErrorInfo/PreCheckResult i et 200-svar — begge tjekkes.
    Returnerer {"ok", "error", "conversion_rate", "raw"}. Netværksfri."""
    data = data or {}
    error = None
    if status_code >= 300:
        error = (data.get("Message") or data.get("ErrorInfo", {}).get("Message")
                 or f"HTTP {status_code}")
    elif data.get("ErrorInfo"):
        ei = data["ErrorInfo"]
        error = ei.get("Message") or ei.get("ErrorCode") or str(ei)
    elif data.get("PreCheckResult") and str(data["PreCheckResult"]).lower() != "ok":
        error = f"PreCheckResult={data['PreCheckResult']}"
    disclaimers = data.get("PreTradeDisclaimers")
    if not error and disclaimers and (disclaimers.get("DisclaimerTokens") or []):
        error = ("Saxo kræver at en handels-disclaimer accepteres først — log ind i "
                 "SaxoTraderGO (SIM) og accepter den, og kør så igen.")
    rate = data.get("InstrumentToAccountConversionRate")
    return {"ok": error is None, "error": error,
            "conversion_rate": float(rate) if rate else None, "raw": data}


def precheck(access_token: str, body: dict) -> dict:
    resp = requests.post(f"{API_BASE_URL}/trade/v2/orders/precheck",
                         headers=_headers(access_token), json=body, timeout=TIMEOUT)
    try:
        data = resp.json() if resp.content else {}
    except ValueError:
        data = {"Message": resp.text[:500]}
    return parse_precheck(resp.status_code, data)


def place_order(access_token: str, body: dict) -> dict:
    resp = requests.post(f"{API_BASE_URL}/trade/v2/orders",
                         headers=_headers(access_token), json=body, timeout=TIMEOUT)
    if resp.status_code >= 300:
        raise SaxoApiError(f"Ordre afvist (HTTP {resp.status_code}): {resp.text[:500]}")
    data = resp.json() if resp.content else {}
    if data.get("ErrorInfo"):
        raise SaxoApiError(f"Ordre afvist: {data['ErrorInfo']}")
    return data


def list_open_orders(access_token: str) -> list:
    return _get(access_token, "/port/v1/orders/me").get("Data", [])


def cancel_orders(access_token: str, account_key: str, order_ids: list):
    if not order_ids:
        return
    ids = ",".join(str(i) for i in order_ids)
    resp = requests.delete(f"{API_BASE_URL}/trade/v2/orders/{ids}",
                           headers=_headers(access_token),
                           params={"AccountKey": account_key}, timeout=TIMEOUT)
    if resp.status_code >= 300:
        raise SaxoApiError(f"Annullering fejlede (HTTP {resp.status_code}): {resp.text[:500]}")


# --- Positioner --------------------------------------------------------------

def list_positions(access_token: str) -> list:
    return _get(access_token, "/port/v1/positions/me").get("Data", [])


def aggregate_positions(raw_positions: list) -> dict:
    """Saxo kan have flere åbne positioner i samme instrument (én pr. køb).
    Samler dem pr. Uic: summeret antal og vægtet gennemsnitlig indgangskurs.
    Lukkede/0-positioner springes over. Netværksfri.
    Returnerer {uic: {"uic", "asset_type", "shares", "entry_price", "entry_date"}}."""
    out = {}
    for p in raw_positions:
        base = p.get("PositionBase") or {}
        uic, amount = base.get("Uic"), base.get("Amount")
        if uic is None or not amount:
            continue
        if base.get("Status") and base["Status"] not in ("Open",):
            continue
        price = base.get("OpenPrice") or 0.0
        cur = out.setdefault(uic, {"uic": uic, "asset_type": base.get("AssetType"),
                                   "shares": 0.0, "_cost": 0.0, "entry_date": None})
        cur["shares"] += float(amount)
        cur["_cost"] += float(amount) * float(price)
        opened = base.get("ExecutionTimeOpen")
        if opened and (cur["entry_date"] is None or opened < cur["entry_date"]):
            cur["entry_date"] = opened
    for cur in out.values():
        cur["entry_price"] = cur["_cost"] / cur["shares"] if cur["shares"] else 0.0
        del cur["_cost"]
    return out

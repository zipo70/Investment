"""
trading/instrument_mapping.py — Lag 3: Yahoo-ticker -> Saxo Uic/AssetType.

Saxo identificerer instrumenter med et internt tal, "Uic" (Unique
Instrument Code), ikke et børssymbol — og Yahoo-tickeren i "Mine Aktier"
(fx "NOVO-B.CO") svarer ikke direkte til noget Saxo forstår. Opslaget kan
også være tvetydigt (samme selskab noteret flere steder/i flere
valutaer), så vi er BEVIDST strenge: findes der mere end ét træf, fejler
vi hellere end at gætte forkert.

Resultatet caches i trading.db (instrument_cache-tabellen, se db.py), så
du typisk kun skal bekræfte/rette mappingen manuelt én gang pr. ticker."""

import os

import requests

API_BASE_URL = os.environ.get("SAXO_API_BASE_URL", "https://gateway.saxobank.com/sim/openapi")


class MappingError(Exception):
    pass


def lookup_uic(access_token: str, yahoo_ticker: str, conn) -> dict:
    """Returnerer {"uic": int, "asset_type": str, "currency": str, "symbol": str}.
    `conn` er en åben trading.db-forbindelse (se db.connect()) — bruges
    både til cache-opslag og til at gemme et nyt træf."""
    from trading import db  # lokal import for at undgå cirkulær afhængighed

    cached = db.get_cached_instrument(conn, yahoo_ticker)
    if cached:
        return cached

    query = _yahoo_ticker_to_search_term(yahoo_ticker)
    resp = requests.get(
        f"{API_BASE_URL}/ref/v1/instruments",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"Keywords": query, "AssetTypes": "Stock"},
        timeout=20,
    )
    if resp.status_code >= 300:
        raise MappingError(
            f"Instrument-opslag hos Saxo fejlede for {yahoo_ticker!r} "
            f"(HTTP {resp.status_code}): {resp.text}"
        )
    results = resp.json().get("Data", [])
    if not results:
        raise MappingError(
            f"Intet Saxo-instrument fundet for {yahoo_ticker!r} (søgte: {query!r}). "
            "Tilføj evt. en manuel oversættelse i stedet for at regne med søgning."
        )
    if len(results) > 1:
        # Bevidst strengt: et forkert, automatisk valgt instrument (fx
        # forkert børs/valuta for samme selskab) er værre end en fejl du
        # selv skal rette manuelt én gang.
        raise MappingError(
            f"{len(results)} mulige Saxo-instrumenter fundet for {yahoo_ticker!r} — "
            "tilføj en eksplicit mapping (fx en lille opslagstabel i denne fil) "
            "i stedet for at gætte hvilket der er det rigtige."
        )

    best = results[0]
    mapping = {
        "uic": best["Identifier"],
        "asset_type": best["AssetType"],
        "currency": best.get("CurrencyCode"),
        "symbol": best.get("Symbol"),
    }
    db.cache_instrument(conn, yahoo_ticker, **mapping)
    return mapping


def _yahoo_ticker_to_search_term(yahoo_ticker: str) -> str:
    """Meget forenklet — fjerner blot Yahoos børssuffiks (".CO", ".ST"
    osv.). UDVID/ERSTAT dette med en eksplicit tabel for de tickere du
    faktisk handler — det er langt mere robust end at stole på at en
    automatisk tekstsøgning rammer det rigtige instrument hver gang.
    Overvej fx en dict {yahoo_ticker: (uic, asset_type)} du selv
    vedligeholder, og slå kun op via Saxos søge-API som fallback."""
    return yahoo_ticker.split(".")[0]

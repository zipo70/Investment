"""
trading/instrument_mapping.py — Lag 3: Yahoo-ticker -> Saxo Uic/AssetType.

Saxo identificerer instrumenter med et internt tal, "Uic", ikke et
børssymbol. Opslaget sker i tre trin:

  1. Cache i trading.db (instrument_cache) — hurtigt, og et tidligere
     bekræftet træf genbruges.
  2. Manuel oversættelse i trading/instrument_overrides.json — vinder over
     søgning. Format: {"NOVO-B.CO": {"uic": 15629, "asset_type": "Stock"}}
  3. Søgning hos Saxo, filtreret på BØRS: Yahoo-suffikset (".CO", ".ST" …)
     oversættes til børsens MIC-kode, og kun træf hvis Saxo-symbol ender på
     den (fx "NOVOb:xcse") beholdes. Er der stadig flere end ét, fejler vi
     bevidst med en liste over kandidaterne, så du kan tilføje den rigtige
     i overrides-filen — et forkert instrument er værre end en fejl.
"""

import json
import os
from pathlib import Path

import requests

API_BASE_URL = os.environ.get("SAXO_API_BASE_URL", "https://gateway.saxobank.com/sim/openapi")
OVERRIDES_FILE = Path(__file__).resolve().parent / "instrument_overrides.json"

# Yahoo-suffiks -> MIC-koder (Saxo-symboler slutter på ":<mic>" med små bogstaver).
SUFFIX_TO_MICS = {
    "": ("xnas", "xnys", "arcx", "bats"),   # USA (ingen suffiks hos Yahoo)
    "CO": ("xcse",), "ST": ("xsto",), "OL": ("xosl",), "HE": ("xhel",),
    "DE": ("xetr",), "F": ("xfra",), "AS": ("xams",), "PA": ("xpar",),
    "BR": ("xbru",), "MI": ("xmil",), "MC": ("xmad",), "L": ("xlon",),
    "SW": ("xswx", "xvtx"), "VI": ("xwbo",), "LS": ("xlis",), "IR": ("xdub",),
    "TO": ("xtse",), "HK": ("xhkg",), "T": ("xtks",), "AX": ("xasx",),
}


class MappingError(Exception):
    pass


def split_yahoo_ticker(yahoo_ticker: str):
    """"NOVO-B.CO" -> ("NOVO", "B", "CO"); "AAPL" -> ("AAPL", None, "");
    "BRK-B" -> ("BRK", "B", "")."""
    sym, _, suffix = yahoo_ticker.upper().partition(".")
    root, _, share_class = sym.partition("-")
    return root, (share_class or None), suffix


def filter_candidates(yahoo_ticker: str, results: list) -> list:
    """Beholder kun Saxo-træf på den rigtige børs og (hvis relevant) den
    rigtige aktieklasse. Netværksfri, så den kan unit-testes."""
    root, share_class, suffix = split_yahoo_ticker(yahoo_ticker)
    mics = SUFFIX_TO_MICS.get(suffix)
    out = []
    for r in results:
        symbol = str(r.get("Symbol", ""))
        sym_root, _, mic = symbol.partition(":")
        if mics is not None and mic.lower() not in mics:
            continue
        sym_norm = sym_root.upper().replace(".", "").replace("_", "")
        expected = root + (share_class or "")
        if sym_norm != expected and sym_norm != root:
            continue
        if share_class and sym_norm == root and len(results) > 1:
            continue  # klasse-løst symbol, når vi leder efter en bestemt klasse
        out.append(r)
    return out


def _load_overrides() -> dict:
    if not OVERRIDES_FILE.exists():
        return {}
    try:
        return json.loads(OVERRIDES_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise MappingError(f"Kunne ikke læse {OVERRIDES_FILE.name}: {e}")


def lookup_uic(access_token: str, yahoo_ticker: str, conn) -> dict:
    """Returnerer {"uic", "asset_type", "currency", "symbol"}."""
    from trading import db  # lokal import for at undgå cirkulær afhængighed

    override = _load_overrides().get(yahoo_ticker)
    if override:
        mapping = {"uic": int(override["uic"]), "asset_type": override.get("asset_type", "Stock"),
                   "currency": override.get("currency"), "symbol": override.get("symbol")}
        db.cache_instrument(conn, yahoo_ticker, **mapping)
        return mapping

    cached = db.get_cached_instrument(conn, yahoo_ticker)
    if cached:
        return {k: cached[k] for k in ("uic", "asset_type", "currency", "symbol")}

    root, _, _ = split_yahoo_ticker(yahoo_ticker)
    resp = requests.get(
        f"{API_BASE_URL}/ref/v1/instruments",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"Keywords": root, "AssetTypes": "Stock", "$top": 50},
        timeout=20,
    )
    if resp.status_code >= 300:
        raise MappingError(f"Instrument-opslag fejlede for {yahoo_ticker!r} "
                           f"(HTTP {resp.status_code}): {resp.text[:300]}")
    results = resp.json().get("Data", [])
    matches = filter_candidates(yahoo_ticker, results)

    if len(matches) != 1:
        shown = ", ".join(f"{r.get('Symbol')} (Uic {r.get('Identifier')})" for r in (matches or results)[:6])
        why = "Intet træf på den rigtige børs" if not matches else f"{len(matches)} mulige træf"
        raise MappingError(
            f"{why} for {yahoo_ticker!r}. Kandidater: {shown or 'ingen'}. "
            f"Tilføj den rigtige i trading/instrument_overrides.json, fx "
            f'{{"{yahoo_ticker}": {{"uic": 12345, "asset_type": "Stock"}}}}'
        )

    best = matches[0]
    mapping = {"uic": best["Identifier"], "asset_type": best["AssetType"],
               "currency": best.get("CurrencyCode"), "symbol": best.get("Symbol")}
    db.cache_instrument(conn, yahoo_ticker, **mapping)
    return mapping

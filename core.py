"""
core.py — delt analyselogik for "Mine Aktier".

Al ren analyselogik (INGEN Streamlit/GUI-kode) bor her, så den kan genbruges
af to forskellige "indgange":

- `app.py` — mobil-app-GUI'en, deployet til Streamlit Community Cloud.
  Lægger kun st.cache_data-caching ovenpå funktionerne herfra.
- `alert_check.py` — det automatiske daglige køb/sælg-tjek, kørt via
  GitHub Actions. Kører IKKE i et Streamlit-miljø, og kan derfor ikke
  bruge st.cache_data eller st.secrets direkte.

Streamlit importeres derfor kun defensivt (i `_get_secret`, og kun hvis den
er installeret) — resten af filen har ingen hård Streamlit-afhængighed,
hvilket holder GitHub Actions-jobbet let og hurtigt at sætte op.
"""

import os
import time
import datetime

import numpy as np
import pandas as pd


# ============================================================================
# --- Config ---
# ============================================================================

class Config:
    trend_sma_window = 200
    rsi_window = 14
    macd_fast = 12
    macd_slow = 26
    macd_signal = 9
    cross_lookback_days = 10
    high_lookback_days = 5
    momentum_shift_lookback_days = 5
    volume_spike_window = 20
    volume_spike_threshold = 2.0
    # --- Komposit-vægtning (teknisk / fundamental / popularitet / Aktieguld) ---
    # Oprindeligt teknisk 30% / fundamental 25% / popularitet 45%. Aktieguld
    # (jf. bruger, 2026-09) er tilføjet som en 4. faktor, vægtet 20% — de tre
    # oprindelige er nedskaleret proportionalt for at give plads (30→24,
    # 25→20, 45→36), ikke erstattet. Se get_aktieguld_data() for metoden.
    weight_ta = 0.24
    weight_fundamental = 0.20
    weight_popularity = 0.36   # vægtet tungest — populæritet/tiltro har stor effekt (jf. bruger)
    weight_aktieguld = 0.20
    long_term_lookback_days = 252   # ~12 måneder, til langsigtet momentum-trigger
    period_high_proximity_pct = 5.0  # "tæt på flerårs-højeste" = inden for X% af perioden-høj
    reddit_subreddits = ["stocks", "investing", "wallstreetbets"]
    reddit_time_filter = "week"
    reddit_search_limit = 15
    min_history_days = 220
    screener_shortlist_size = 20       # kun de N bedste TA-kandidater går videre
                                        # til analytiker-/sentiment-opslag
    sentiment_request_delay_sec = 1.0  # høflighedspause mellem API-kald i Top 10-scan
    scan_request_delay_sec = 0.3        # kort høflighedspause i trin 1 (fuld-univers-
                                         # scanning, ~93 tickere) — mindsker risikoen
                                         # for rate-limitering fra Yahoo (se BACKLOG.md #3)
    scan_skipped_data_warn_ratio = 0.2   # advar i GUI'en hvis andelen af "kunne ikke
                                         # hentes" i en scanning overstiger denne andel
                                         # af universet — tyder på rate-limitering, ikke
                                         # et roligt marked (se BACKLOG.md #3)
    swing_hold_days = 20                # maks. holdeperiode (handelsdage) — bruges
                                         # til "stopud dato" i Top 10-fanen
    entry_window_days = 3               # indgangsforslaget regnes som "gyldigt" i
                                         # så mange handelsdage, før signalet er stale
    stop_loss_max_pct = 0.10            # loft over tab: stop-loss må aldrig indebære
                                         # mere end 10% tab fra indgangskursen, uanset
                                         # hvor dybt det tekniske niveau (bund/SMA50)
                                         # ellers ligger (jf. bruger)


# ============================================================================
# --- Univers (samme kuraterede liste som kandidat-screeneren) ---
# ============================================================================

US = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "ORCL",
    "CRM", "ADBE", "NFLX", "COST", "WMT", "HD", "PG", "KO", "PEP", "JNJ",
    "UNH", "PFE", "JPM", "BAC", "V", "MA", "XOM", "CVX", "DIS", "INTC", "CSCO",
]

EUROPE = [
    "ASML.AS", "SAP.DE", "MC.PA", "OR.PA", "TTE.PA", "AIR.PA", "SAN.PA",
    "SIE.DE", "ALV.DE", "BAS.DE", "NOVN.SW", "NESN.SW", "ROG.SW", "AZN.L",
    "SHEL.L", "HSBA.L", "ULVR.L", "IBE.MC",
]

NORDIC = [
    "NOVO-B.CO", "MAERSK-B.CO", "VWS.CO", "ORSTED.CO", "DSV.CO", "GMAB.CO",
    "COLO-B.CO", "ERIC-B.ST", "VOLV-B.ST", "ATCO-A.ST", "INVE-B.ST",
    "HM-B.ST", "SAND.ST", "EQNR.OL", "DNB.OL", "TEL.OL", "NOKIA.HE", "SAMPO.HE",
]

EMERGING_MARKETS = [
    "TSM", "BABA", "PDD", "JD", "TCEHY", "INFY", "IBN", "HDB", "MELI",
    "VALE", "ITUB", "PBR", "AMX",
]

BENCHMARK = "ACWI"


def full_universe():
    """Hele det kuraterede univers (alle regioner), dedupliceret."""
    seen, out = set(), []
    for group in (US, EUROPE, NORDIC, EMERGING_MARKETS):
        for t in group:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out


def region_of(ticker):
    if ticker in US:
        return "USA"
    if ticker in EUROPE:
        return "Europa"
    if ticker in NORDIC:
        return "Norden"
    if ticker in EMERGING_MARKETS:
        return "Emerging Markets"
    return "Ukendt"


# ============================================================================
# --- Datahentning ---
# ============================================================================

def fetch_ticker_df(ticker: str, period: str = "5y"):
    """Henter kursdata for én ticker.

    Retry-strategi (rettet — se BACKLOG.md #1): et TOMT svar UDEN exception
    betyder næsten altid at tickeren ikke findes/er afnoteret/er stavet
    forkert — at prøve igen hjælper ikke der, det spilder bare tid (op til
    6 sekunder pr. "død" ticker, hvilket lagde sig markant oveni en
    fuld-univers-scanning på ~93 tickere). Vi springer derfor MED DET SAMME
    ved et tomt svar, og gemmer kun retry+backoff til RIGTIGE exceptions
    (netværksfejl, timeout, rate-limiting), hvor et nyt forsøg faktisk kan
    lykkes."""
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance ikke installeret"
    last_err = None
    for attempt in range(3):
        try:
            df = yf.download(ticker, period=period, auto_adjust=False, progress=False, threads=False)
            if df is None or df.empty:
                return None, "ingen data (tickeren findes muligvis ikke, eller er afnoteret)"
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.rename(columns={"Adj Close": "AdjClose"})
            return df, None
        except Exception as e:
            last_err = str(e)
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
    return None, last_err


# Statisk navn→ticker-opslag for hele det kuraterede univers (rettet — se
# BACKLOG.md #2). Bruges som fallback, HVIS Yahoos uofficielle søge-API
# skulle fejle/blive blokeret/rate-limitet — det er en ekstern, ugaranteret
# tjeneste, så en indbygget liste over vores eget univers virker uafhængigt
# af om den er oppe. Aliaser er små bogstaver, uden accenter/specialtegn.
_NAME_ALIASES = {
    "AAPL": (["apple"], "Apple Inc.", "NASDAQ"),
    "MSFT": (["microsoft"], "Microsoft Corp.", "NASDAQ"),
    "NVDA": (["nvidia"], "NVIDIA Corp.", "NASDAQ"),
    "AMZN": (["amazon"], "Amazon.com Inc.", "NASDAQ"),
    "GOOGL": (["google", "alphabet"], "Alphabet Inc.", "NASDAQ"),
    "META": (["meta", "facebook"], "Meta Platforms Inc.", "NASDAQ"),
    "TSLA": (["tesla"], "Tesla Inc.", "NASDAQ"),
    "AVGO": (["broadcom"], "Broadcom Inc.", "NASDAQ"),
    "ORCL": (["oracle"], "Oracle Corp.", "NYSE"),
    "CRM": (["salesforce"], "Salesforce Inc.", "NYSE"),
    "ADBE": (["adobe"], "Adobe Inc.", "NASDAQ"),
    "NFLX": (["netflix"], "Netflix Inc.", "NASDAQ"),
    "COST": (["costco"], "Costco Wholesale Corp.", "NASDAQ"),
    "WMT": (["walmart"], "Walmart Inc.", "NYSE"),
    "HD": (["home depot"], "Home Depot Inc.", "NYSE"),
    "PG": (["procter", "procter gamble", "procter & gamble"], "Procter & Gamble Co.", "NYSE"),
    "KO": (["coca cola", "coca-cola", "cocacola"], "Coca-Cola Co.", "NYSE"),
    "PEP": (["pepsi", "pepsico"], "PepsiCo Inc.", "NASDAQ"),
    "JNJ": (["johnson", "johnson & johnson", "johnson og johnson"], "Johnson & Johnson", "NYSE"),
    "UNH": (["unitedhealth", "united health"], "UnitedHealth Group Inc.", "NYSE"),
    "PFE": (["pfizer"], "Pfizer Inc.", "NYSE"),
    "JPM": (["jpmorgan", "jp morgan"], "JPMorgan Chase & Co.", "NYSE"),
    "BAC": (["bank of america"], "Bank of America Corp.", "NYSE"),
    "V": (["visa"], "Visa Inc.", "NYSE"),
    "MA": (["mastercard"], "Mastercard Inc.", "NYSE"),
    "XOM": (["exxon", "exxonmobil", "exxon mobil"], "Exxon Mobil Corp.", "NYSE"),
    "CVX": (["chevron"], "Chevron Corp.", "NYSE"),
    "DIS": (["disney"], "Walt Disney Co.", "NYSE"),
    "INTC": (["intel"], "Intel Corp.", "NASDAQ"),
    "CSCO": (["cisco"], "Cisco Systems Inc.", "NASDAQ"),
    "ASML.AS": (["asml"], "ASML Holding", "Amsterdam"),
    "SAP.DE": (["sap"], "SAP SE", "Frankfurt"),
    "MC.PA": (["lvmh"], "LVMH", "Paris"),
    "OR.PA": (["loreal", "l'oreal", "l'oréal"], "L'Oréal", "Paris"),
    "TTE.PA": (["totalenergies", "total energies", "total"], "TotalEnergies", "Paris"),
    "AIR.PA": (["airbus"], "Airbus SE", "Paris"),
    "SAN.PA": (["sanofi"], "Sanofi", "Paris"),
    "SIE.DE": (["siemens"], "Siemens AG", "Frankfurt"),
    "ALV.DE": (["allianz"], "Allianz SE", "Frankfurt"),
    "BAS.DE": (["basf"], "BASF SE", "Frankfurt"),
    "NOVN.SW": (["novartis"], "Novartis AG", "Zürich"),
    "NESN.SW": (["nestle", "nestlé"], "Nestlé SA", "Zürich"),
    "ROG.SW": (["roche"], "Roche Holding AG", "Zürich"),
    "AZN.L": (["astrazeneca", "astra zeneca"], "AstraZeneca", "London"),
    "SHEL.L": (["shell"], "Shell plc", "London"),
    "HSBA.L": (["hsbc"], "HSBC Holdings", "London"),
    "ULVR.L": (["unilever"], "Unilever plc", "London"),
    "IBE.MC": (["iberdrola"], "Iberdrola SA", "Madrid"),
    "NOVO-B.CO": (["novo", "novo nordisk", "novonordisk"], "Novo Nordisk", "København"),
    "MAERSK-B.CO": (["maersk", "mærsk", "moller maersk", "møller mærsk", "ap moller"], "A.P. Møller - Mærsk", "København"),
    "VWS.CO": (["vestas"], "Vestas Wind Systems", "København"),
    "ORSTED.CO": (["orsted", "ørsted"], "Ørsted", "København"),
    "DSV.CO": (["dsv"], "DSV", "København"),
    "GMAB.CO": (["genmab"], "Genmab", "København"),
    "COLO-B.CO": (["coloplast"], "Coloplast", "København"),
    "ERIC-B.ST": (["ericsson"], "Ericsson", "Stockholm"),
    "VOLV-B.ST": (["volvo"], "Volvo AB", "Stockholm"),
    "ATCO-A.ST": (["atlas copco"], "Atlas Copco", "Stockholm"),
    "INVE-B.ST": (["investor ab", "investor"], "Investor AB", "Stockholm"),
    "HM-B.ST": (["h&m", "hm", "hennes mauritz", "hennes & mauritz"], "H&M", "Stockholm"),
    "SAND.ST": (["sandvik"], "Sandvik AB", "Stockholm"),
    "EQNR.OL": (["equinor"], "Equinor ASA", "Oslo"),
    "DNB.OL": (["dnb"], "DNB Bank ASA", "Oslo"),
    "TEL.OL": (["telenor"], "Telenor ASA", "Oslo"),
    "NOKIA.HE": (["nokia"], "Nokia Oyj", "Helsinki"),
    "SAMPO.HE": (["sampo"], "Sampo Oyj", "Helsinki"),
    "TSM": (["taiwan semiconductor", "tsmc"], "Taiwan Semiconductor (ADR)", "NYSE"),
    "BABA": (["alibaba"], "Alibaba Group (ADR)", "NYSE"),
    "PDD": (["pdd", "pinduoduo", "temu"], "PDD Holdings (ADR)", "NASDAQ"),
    "JD": (["jd.com", "jd com"], "JD.com Inc. (ADR)", "NASDAQ"),
    "TCEHY": (["tencent"], "Tencent Holdings (ADR)", "OTC"),
    "INFY": (["infosys"], "Infosys Ltd. (ADR)", "NYSE"),
    "IBN": (["icici", "icici bank"], "ICICI Bank (ADR)", "NYSE"),
    "HDB": (["hdfc", "hdfc bank"], "HDFC Bank (ADR)", "NYSE"),
    "MELI": (["mercadolibre", "mercado libre"], "MercadoLibre Inc.", "NASDAQ"),
    "VALE": (["vale"], "Vale SA", "NYSE"),
    "ITUB": (["itau", "itaú", "itau unibanco"], "Itaú Unibanco (ADR)", "NYSE"),
    "PBR": (["petrobras"], "Petrobras (ADR)", "NYSE"),
    "AMX": (["america movil", "américa móvil"], "América Móvil (ADR)", "NYSE"),
}


def _match_static_alias(query: str):
    """Matcher en fri søgetekst mod _NAME_ALIASES. Tjekker i rækkefølge:
    (1) tickeren selv skrevet direkte, (2) et præcist aliasmatch, (3) et
    løsere delvist match — så det mest sikre match altid vinder."""
    q = " ".join(query.strip().lower().split())
    if not q:
        return None
    for ticker, (aliases, name, exch) in _NAME_ALIASES.items():
        if q == ticker.lower():
            return ticker, name, exch
    for ticker, (aliases, name, exch) in _NAME_ALIASES.items():
        if q in aliases:
            return ticker, name, exch
    for ticker, (aliases, name, exch) in _NAME_ALIASES.items():
        if any(a.startswith(q) or q in a for a in aliases):
            return ticker, name, exch
    return None


def resolve_ticker(query: str):
    """Slår et frit navn/ticker op, så man kan skrive fx "novo" i stedet for
    at kende den præcise Yahoo-ticker-syntaks (NOVO-B.CO). To trin:

    1) Yahoo Finances (uofficielle) søge-API — dækker langt flere aktier
       end vores eget univers, men er ugaranteret og kan fejle/blive
       rate-limitet/blokeret uden varsel (se BACKLOG.md #2).
    2) Falder tilbage til en indbygget navn-liste for det kuraterede
       univers (~93 aktier), som virker uafhængigt af Yahoos søge-API.

    Returnerer (symbol, visningsnavn, børs) — eller (None, None, None) hvis
    intet findes, så kaldende kode altid kan falde tilbage til at bruge
    inputtet som ticker direkte. Fejl fra trin 1 logges (synligt i appens
    driftslog), i stedet for at forsvinde stille."""
    try:
        import requests
        resp = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 6, "newsCount": 0, "lang": "en-US"},
            headers={"User-Agent": "Mozilla/5.0 (Mine Aktier; personligt værktøj)"},
            timeout=6,
        )
        if resp.status_code == 200:
            quotes = [q for q in (resp.json().get("quotes") or []) if q.get("symbol")]
            equities = [q for q in quotes if q.get("quoteType") == "EQUITY"] or quotes
            if equities:
                top = equities[0]
                name = top.get("shortname") or top.get("longname") or top["symbol"]
                exchange = top.get("exchDisp") or top.get("exchange")
                return top["symbol"], name, exchange
            print(f"resolve_ticker: Yahoo-søgning gav ingen aktie-resultater for '{query}' — prøver indbygget liste.")
        else:
            print(f"resolve_ticker: Yahoo-søgning svarede HTTP {resp.status_code} for '{query}' — prøver indbygget liste.")
    except ImportError:
        pass
    except Exception as e:
        print(f"resolve_ticker: Yahoo-søgning fejlede for '{query}' ({e}) — prøver indbygget liste.")

    match = _match_static_alias(query)
    if match:
        return match
    return None, None, None


def get_ticker_meta(ticker: str) -> dict:
    """Børs/land/valuta for én ticker — vises som label på hvert kort, så det
    altid er tydeligt hvilket marked man kigger på (fx ved flere noteringer
    af samme selskab, som Novo Nordisk i både København og New York)."""
    try:
        import yfinance as yf
    except ImportError:
        return {}
    try:
        info = yf.Ticker(ticker).info or {}
    except Exception:
        return {}
    return {
        "exchange": info.get("fullExchangeName") or info.get("exchange"),
        "country": info.get("country"),
        "currency": info.get("currency"),
    }


# ============================================================================
# --- Tekniske signaler ---
# ============================================================================

def sma(series, window):
    return series.rolling(window, min_periods=window).mean()


def rsi(series, window=14):
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)


def macd(series, fast=12, slow=26, signal=9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    return macd_line - signal_line


def _recent_event(bool_series, lookback_days):
    tail = bool_series.tail(lookback_days)
    hits = tail[tail == True]  # noqa: E712
    if hits.empty:
        return False, None
    last_idx = bool_series.index.get_loc(hits.index[-1])
    return True, (len(bool_series) - 1) - last_idx


def compute_technical_signals(df, cfg):
    close = df["Close"].dropna()
    volume = df["Volume"].dropna() if "Volume" in df.columns else pd.Series(dtype=float)
    needed = max(cfg.trend_sma_window, 252) + 5
    if len(close) < needed:
        return {"ok": False, "reason": f"utilstrækkelig historik ({len(close)} dage)"}

    sma50, sma200 = sma(close, 50), sma(close, cfg.trend_sma_window)
    last_price, last_sma200 = float(close.iloc[-1]), float(sma200.iloc[-1])
    trend_ok = last_price > last_sma200

    cross_sign = np.sign((sma50 - sma200).dropna())
    cross_flip = cross_sign != cross_sign.shift(1)
    golden_hit, golden_days_ago = _recent_event(cross_flip & (cross_sign > 0), cfg.cross_lookback_days)
    death_hit, death_days_ago = _recent_event(cross_flip & (cross_sign < 0), cfg.cross_lookback_days)

    prior_high_252 = close.shift(1).rolling(252, min_periods=200).max()
    breakout_hit, breakout_days_ago = _recent_event(close > prior_high_252, cfg.high_lookback_days)

    prior_low_252 = close.shift(1).rolling(252, min_periods=200).min()
    breakdown_hit, breakdown_days_ago = _recent_event(close < prior_low_252, cfg.high_lookback_days)

    rsi_series = rsi(close, cfg.rsi_window)
    rsi_cross_up = (rsi_series > 30) & (rsi_series.shift(1) <= 30)
    rsi_hit, rsi_days_ago = _recent_event(rsi_cross_up, cfg.momentum_shift_lookback_days)
    rsi_cross_down = (rsi_series < 70) & (rsi_series.shift(1) >= 70)
    rsi_bear_hit, rsi_bear_days_ago = _recent_event(rsi_cross_down, cfg.momentum_shift_lookback_days)

    macd_hist = macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    macd_flip_up = (macd_hist > 0) & (macd_hist.shift(1) <= 0)
    macd_hit, macd_days_ago = _recent_event(macd_flip_up, cfg.momentum_shift_lookback_days)
    macd_flip_down = (macd_hist < 0) & (macd_hist.shift(1) >= 0)
    macd_bear_hit, macd_bear_days_ago = _recent_event(macd_flip_down, cfg.momentum_shift_lookback_days)

    momentum_hit = rsi_hit or macd_hit
    momentum_bear_hit = rsi_bear_hit or macd_bear_hit

    avg_vol_20 = volume.rolling(cfg.volume_spike_window, min_periods=cfg.volume_spike_window).mean()
    last_vol = float(volume.iloc[-1]) if not volume.empty else float("nan")
    last_avg_vol = float(avg_vol_20.iloc[-1]) if not avg_vol_20.empty else float("nan")
    vol_ratio = (last_vol / last_avg_vol) if last_avg_vol and not np.isnan(last_avg_vol) and last_avg_vol > 0 else float("nan")
    vol_spike = bool(vol_ratio and not np.isnan(vol_ratio) and vol_ratio >= cfg.volume_spike_threshold)

    # --- Købs-triggere ---
    triggers, score = [], 0
    if trend_ok:
        score += 30
        if golden_hit:
            score += 25; triggers.append(f"Golden cross for {golden_days_ago} dage siden")
        if breakout_hit:
            score += 25; triggers.append(f"Nyt 52-ugers højeste for {breakout_days_ago} dage siden")
        if momentum_hit:
            score += 15
            src = "RSI-rebound" if rsi_hit else "MACD-momentumskift"
            triggers.append(f"{src} for {(rsi_days_ago if rsi_hit else macd_days_ago)} dage siden")
        if vol_spike:
            score += 5; triggers.append(f"Volumenspike ({vol_ratio:.1f}x snit)")
    else:
        triggers.append("Under 200-dages glidende gennemsnit")
        if death_hit:
            triggers.append(f"Death cross for {death_days_ago} dage siden")

    # --- Sælg-triggere (symmetrisk med købs-triggerne ovenfor) ---
    sell_triggers, sell_score = [], 0
    if not trend_ok:
        sell_score += 30
        sell_triggers.append("Kurs under 200-dages glidende gennemsnit")
    if death_hit:
        sell_score += 30
        sell_triggers.append(f"Death cross for {death_days_ago} dage siden")
    if breakdown_hit:
        sell_score += 25
        sell_triggers.append(f"Nyt 52-ugers laveste for {breakdown_days_ago} dage siden")
    if momentum_bear_hit:
        sell_score += 15
        src = "RSI-rollover (fra over 70)" if rsi_bear_hit else "MACD-momentumskift (negativt)"
        sell_triggers.append(f"{src} for {(rsi_bear_days_ago if rsi_bear_hit else macd_bear_days_ago)} dage siden")

    return {
        "ok": True, "last_price": last_price, "trend_ok": bool(trend_ok),
        "high_252w": float(close.tail(252).max()), "low_252w": float(close.tail(252).min()),
        "ta_score": min(score, 100), "ta_triggers": triggers,
        "sell_score": min(sell_score, 100), "sell_triggers": sell_triggers,
    }


def compute_long_term_signals(df, cfg) -> dict:
    """Langsigtede ("lang bane") triggere, som supplement til de kortsigtede
    ovenfor (dage/uger): 12-måneders prismomentum og nærhed til periodens
    højeste kurs (bygger på den hentede historik, som er 5 år som standard —
    ikke bogstaveligt "all-time-high", men langt længere end det kortsigtede
    52-ugers-blik). Returnerer {"ok": False} hvis der ikke er nok historik
    til et meningsfyldt 12-måneders sammenligningspunkt."""
    close = df["Close"].dropna()
    lookback = cfg.long_term_lookback_days
    if len(close) < lookback + 5:
        return {"ok": False, "reason": "utilstrækkelig historik til langsigtet analyse"}

    last_price = float(close.iloc[-1])
    prior_price = float(close.iloc[-(lookback + 1)])
    momentum_12m = (last_price / prior_price - 1.0) * 100.0 if prior_price else None

    period_high = float(close.max())
    pct_from_high = (last_price / period_high - 1.0) * 100.0 if period_high else None

    triggers, score = [], 0
    if momentum_12m is not None and momentum_12m > 0:
        score += 50
        triggers.append(f"Positivt 12-måneders momentum ({momentum_12m:+.1f}%)")
    if pct_from_high is not None and pct_from_high >= -cfg.period_high_proximity_pct:
        score += 50
        triggers.append(f"Tæt på flerårs-højeste ({pct_from_high:+.1f}% fra toppen, {len(close)} dages historik)")

    return {
        "ok": True, "momentum_12m": momentum_12m, "pct_from_period_high": pct_from_high,
        "long_term_score": min(score, 100), "long_term_triggers": triggers,
    }


# ============================================================================
# --- Analytiker/sentiment ---
# ============================================================================

def get_analyst_data(ticker):
    try:
        import yfinance as yf
    except ImportError:
        return {"ok": False, "reason": "yfinance ikke installeret"}
    try:
        info = {}
        try:
            info = yf.Ticker(ticker).info or {}
        except Exception:
            pass
        target_mean = info.get("targetMeanPrice")
        num_analysts = info.get("numberOfAnalystOpinions")
        rec_mean = info.get("recommendationMean")
        rec_key = info.get("recommendationKey")
        short_name = info.get("shortName") or info.get("longName")
        analyst_score = None
        if rec_mean is not None:
            analyst_score = max(0.0, min(100.0, (5.0 - float(rec_mean)) / 4.0 * 100.0))
        return {
            "ok": True, "short_name": short_name, "target_mean": target_mean,
            "num_analysts": num_analysts, "recommendation_key": rec_key,
            "analyst_score": analyst_score,
        }
    except Exception as e:
        return {"ok": False, "reason": f"kunne ikke hente analytiker-data: {e}"}


def get_fundamental_data(ticker):
    """Rigtige regnskabsnøgletal (ikke bare analytikeranbefaling) — fra
    Yahoo Finances gratis "info"-felter. Dette er en simpel, sektor-uafhængig
    heuristik (sammenligner IKKE en bank mod en tech-aktie på lige fod) —
    brug som groft filter, ikke som præcis værdiansættelse. Dækningen
    varierer meget pr. ticker/marked, ligesom analytiker-data."""
    try:
        import yfinance as yf
    except ImportError:
        return {"ok": False, "reason": "yfinance ikke installeret"}
    try:
        t = yf.Ticker(ticker)
        try:
            info = t.info or {}
        except Exception:
            info = {}
        if not info:
            return {"ok": False, "reason": "ingen nøgletal tilgængelige"}

        pe = info.get("trailingPE")
        peg = info.get("pegRatio") or info.get("trailingPegRatio")
        profit_margin = info.get("profitMargins")          # andel, fx 0.22 = 22%
        revenue_growth = info.get("revenueGrowth")          # andel, år/år
        earnings_growth = info.get("earningsGrowth") or info.get("earningsQuarterlyGrowth")
        roe = info.get("returnOnEquity")
        debt_to_equity = info.get("debtToEquity")           # typisk i procent, fx 45.2 = 0.45x
        sector = info.get("sector")                          # GICS-sektornavn, fx "Technology" — bruges kun til sektor-rotations-badge (se get_sector_signal), indgår ikke i fundamental_score

        # --- Udvidet efter bruger-ønske (Jacobi, 2026-09-18, Genmab-eksempel:
        # "der mangler nogle fundamentale triggere") — flere generelle
        # nøgletal fra Yahoo Finance, ikke konkrete begivenheder/katalysatorer
        # (fx FDA-godkendelser), som brugeren selv bekræftede ikke findes
        # gratis og derfor er fravalgt. ---
        forward_pe = info.get("forwardPE")
        price_to_sales = info.get("priceToSalesTrailing12Months")
        market_cap = info.get("marketCap")
        free_cashflow = info.get("freeCashflow")
        fcf_yield_pct = (free_cashflow / market_cap * 100.0) if (free_cashflow is not None and market_cap) else None
        insiders = info.get("heldPercentInsiders")

        subscores, weights, triggers = [], [], []

        if profit_margin is not None:
            s = max(0.0, min(100.0, profit_margin * 100.0 / 25.0 * 100.0))
            subscores.append(s); weights.append(0.20)
            if profit_margin > 0.15:
                triggers.append(f"Sund overskudsgrad ({profit_margin*100:.0f}%)")

        if revenue_growth is not None:
            s = max(0.0, min(100.0, 50.0 + revenue_growth * 100.0 * 2.5))
            subscores.append(s); weights.append(0.20)
            if revenue_growth > 0.10:
                triggers.append(f"Solid omsætningsvækst ({revenue_growth*100:+.0f}%)")

        if earnings_growth is not None:
            s = max(0.0, min(100.0, 50.0 + earnings_growth * 100.0 * 2.5))
            subscores.append(s); weights.append(0.20)
            if earnings_growth > 0.10:
                triggers.append(f"Solid indtjeningsvækst ({earnings_growth*100:+.0f}%)")

        if roe is not None:
            s = max(0.0, min(100.0, roe * 100.0 / 20.0 * 100.0))
            subscores.append(s); weights.append(0.20)
            if roe > 0.15:
                triggers.append(f"Høj egenkapitalforrentning ({roe*100:.0f}%)")

        if debt_to_equity is not None:
            s = max(0.0, min(100.0, 100.0 - debt_to_equity / 2.0))
            subscores.append(s); weights.append(0.10)
            if debt_to_equity < 50:
                triggers.append(f"Lav gæld/egenkapital ({debt_to_equity:.0f}%)")

        if peg is not None and peg > 0:
            s = max(0.0, min(100.0, 100.0 - (peg - 1.0) * 40.0))
            subscores.append(s); weights.append(0.10)
            if peg < 1.5:
                triggers.append(f"Attraktiv PEG-ratio ({peg:.1f})")

        if forward_pe is not None and forward_pe > 0 and pe is not None and pe > 0:
            # Forward P/E lavere end nuværende P/E -> markedet forventer
            # stigende indtjening fremadrettet (relevant for fx biotek med
            # ventede godkendelser/lancering, hvor nuværende indtjening ikke
            # afspejler det — jf. bruger, Genmab-eksempel).
            forbedring_pct = (pe - forward_pe) / pe * 100.0
            s = max(0.0, min(100.0, 50.0 + forbedring_pct * 2.0))
            subscores.append(s); weights.append(0.10)
            if forbedring_pct > 15:
                triggers.append(
                    f"Forward P/E ({forward_pe:.1f}) markant lavere end nuværende P/E "
                    f"({pe:.1f}) — markedet forventer stigende indtjening"
                )

        if price_to_sales is not None and price_to_sales > 0:
            s = max(0.0, min(100.0, 100.0 - (price_to_sales - 1.0) * 10.0))
            subscores.append(s); weights.append(0.10)
            if price_to_sales < 5:
                triggers.append(f"Attraktiv price/sales-ratio ({price_to_sales:.1f})")

        if fcf_yield_pct is not None:
            s = max(0.0, min(100.0, 50.0 + fcf_yield_pct * 5.0))
            subscores.append(s); weights.append(0.15)
            if fcf_yield_pct > 5:
                triggers.append(f"Solidt frit cash flow-afkast ({fcf_yield_pct:.1f}% af markedsværdi)")

        if insiders is not None:
            s = max(0.0, min(100.0, insiders * 100.0 / 10.0 * 100.0))
            subscores.append(s); weights.append(0.05)
            if insiders > 0.05:
                triggers.append(f"Højt insiderejerskab ({insiders*100:.0f}%)")

        fundamental_score = (
            sum(s * w for s, w in zip(subscores, weights)) / sum(weights) if subscores else None
        )

        # Kommende regnskabsdato — REN INFO, indgår IKKE i fundamental_score
        # eller nogen trigger/score. Dette er IKKE et forsøg på at gengive
        # specifikke begivenheder/katalysatorer (fx FDA-afgørelser, fase 3-
        # udlæsninger) — det findes ikke gratis via Yahoo Finance, og
        # brugeren har selv bekræftet at det er fravalgt. Næste regnskabs-
        # dato er blot den eneste tidsbestemte begivenhed der ér tilgængelig.
        next_earnings_date = None
        try:
            cal = t.calendar
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date")
                if dates:
                    next_earnings_date = str(dates[0] if isinstance(dates, (list, tuple)) else dates)
            elif cal is not None and hasattr(cal, "empty") and not cal.empty and "Earnings Date" in cal.index:
                val = cal.loc["Earnings Date"]
                next_earnings_date = str(val.iloc[0] if hasattr(val, "iloc") else val)
        except Exception:
            pass

        return {
            "ok": True, "pe": pe, "peg": peg, "profit_margin": profit_margin,
            "revenue_growth": revenue_growth, "earnings_growth": earnings_growth,
            "roe": roe, "debt_to_equity": debt_to_equity, "sector": sector,
            "forward_pe": forward_pe, "price_to_sales": price_to_sales,
            "fcf_yield_pct": fcf_yield_pct, "insiders_pct": insiders,
            "next_earnings_date": next_earnings_date,
            "fundamental_score": fundamental_score, "fundamental_triggers": triggers,
        }
    except Exception as e:
        return {"ok": False, "reason": f"kunne ikke hente nøgletal: {e}"}


def get_aktieguld_data(ticker: str, fundamental: dict) -> dict:
    """"Aktieguld"-point: en tilnærmet gengivelse af Jens Løgstrups 4-fase-
    model fra bogen "Aktieguld" (jf. bruger, 2026-09), udregnet fra Yahoo
    Finance-nøgletal i stedet for en manuel/kvalitativ vurdering pr. aktie.

    GENNEMSIGTIGHED (vigtigt at vide): bogens fulde metode er ikke
    offentligt tilgængelig — hverken via websøgning (tjekket 2026-09-17,
    kun boghandler-sider og anmeldelser, ingen gengiver selve modellen)
    eller i brugerens eget eksempel (en Royal Unibrew-analyse fra brugerens
    egen chat, som viste sig at være afbrudt lige før Fase 3's kriterieliste
    kunne læses). Fase 1's 5 spørgsmål og Fase 2's formel er derfor kendt
    ORDRET (fra det eksempel); Fase 3's 7 "kvalitetspunkter" og Fase 4's
    A/B/C/D-tærskler er IKKE kendt ordret og er derfor min egen, tydeligt
    markerede fortolkning — ikke bogens egen formel. Sig til hvis du på et
    tidspunkt finder/kan indsætte den ordrette liste — så rettes det til.

    Fase 1 — Strategisk Analyse (proxy for 4 af de 5 spørgsmål; "vil
    produkterne være relevante om 5+ år" har intet godt Yahoo-nøgletal og
    indgår derfor ikke direkte i denne tilnærmelse):
      - "marked i vækst?"        → omsætningsvækst (revenueGrowth)
      - "lønsomt marked?"        → overskudsgrad (profitMargins)
      - "stigende overskud 5+ år?" → indtjeningsvækst (earningsGrowth,
        proxy — Yahoo giver ikke gratis en 5-års-historik af dette)
      - "modstår hård konkurrence?" → egenkapitalforrentning (ROE), som
        grov proxy for konkurrencemæssig styrke/"moat"

    Fase 2 — Afkastberegning (ORDRET FORMEL fra brugerens eksempel):
      Forventet afkast % = Overskudsvækst % + Aktietilbagekøb % + Udbytte %
      Aktietilbagekøb hentes fra cashflow-opgørelsen — kun her ved den
      dybdegående enkelt-aktie-analyse, IKKE i fuld-univers-scanningen
      (find_top_candidates trin 1), for ikke at øge risikoen for
      rate-limitering (se BACKLOG.md #3).

    Fase 3 — Kvalitetspoint (MIN EGEN FORTOLKNING, ikke bogens 7 punkter):
      insider-ejerskab, institutionelt ejerskab, lav gæld/egenkapital,
      en sund udlodningsgrad, og lav beta (kursstabilitet) — som
      stedfortrædere for stikordene i brugerens eksempel ("stærk ledelse",
      "fokus", "robusthed", "udlodning").

    Fase 4 — Vedligehold/Konklusion: forsøges IKKE gengivet som en separat
    A/B/C/D-bogstav-konklusion, da tærsklerne er ukendte — den rolle
    varetages i stedet af appens eksisterende KØB/HOLD/SÆLG-anbefaling
    (compute_recommendation).

    De tre fase-scorer (hver 0-100) vægtes ligeligt til én samlet
    `aktieguld_score` (0-100), da bogen ikke angiver en indbyrdes vægtning
    mellem faserne."""
    try:
        import yfinance as yf
    except ImportError:
        return {"ok": False, "reason": "yfinance ikke installeret"}
    try:
        t = yf.Ticker(ticker)
        try:
            info = t.info or {}
        except Exception:
            info = {}
        if not info:
            return {"ok": False, "reason": "ingen nøgletal tilgængelige"}

        triggers = []
        fa_ok = fundamental.get("ok", False)

        # --- Fase 1: Strategisk Analyse ---
        revenue_growth = fundamental.get("revenue_growth") if fa_ok else info.get("revenueGrowth")
        profit_margin = fundamental.get("profit_margin") if fa_ok else info.get("profitMargins")
        earnings_growth = fundamental.get("earnings_growth") if fa_ok else info.get("earningsGrowth")
        roe = fundamental.get("roe") if fa_ok else info.get("returnOnEquity")

        fase1_scores = []
        if revenue_growth is not None:
            fase1_scores.append(max(0.0, min(100.0, 50.0 + revenue_growth * 100.0 * 2.5)))
        if profit_margin is not None:
            fase1_scores.append(max(0.0, min(100.0, profit_margin * 100.0 / 25.0 * 100.0)))
        if earnings_growth is not None:
            fase1_scores.append(max(0.0, min(100.0, 50.0 + earnings_growth * 100.0 * 2.5)))
        if roe is not None:
            fase1_scores.append(max(0.0, min(100.0, roe * 100.0 / 20.0 * 100.0)))
        fase1_score = sum(fase1_scores) / len(fase1_scores) if fase1_scores else None
        if fase1_score is not None and fase1_score >= 65:
            triggers.append(f"Stærk strategisk position (Fase 1: {fase1_score:.0f}/100)")

        # --- Fase 2: Afkastberegning (ordret formel) ---
        overskudsvaekst_pct = (
            earnings_growth * 100.0 if earnings_growth is not None
            else (revenue_growth * 100.0 if revenue_growth is not None else None)
        )

        buyback_pct = None
        try:
            cf = t.cashflow
            market_cap = info.get("marketCap")
            if cf is not None and not cf.empty and market_cap:
                buyback_row = None
                for label in ("Repurchase Of Capital Stock", "Repurchase Of Common Stock",
                              "CommonStockRepurchased", "Repurchase Of Stock"):
                    if label in cf.index:
                        buyback_row = cf.loc[label]
                        break
                if buyback_row is not None and len(buyback_row) > 0:
                    latest = buyback_row.iloc[0]
                    if pd.notna(latest):
                        buyback_pct = abs(float(latest)) / market_cap * 100.0
        except Exception:
            pass

        udbytte_pct = None
        dy = info.get("dividendYield")
        if dy is not None:
            # Yahoo har historisk skiftet mellem andel (0.03) og procent (3.0)
            # for dette felt afhængigt af version — normaliser til procent.
            udbytte_pct = dy * 100.0 if dy < 1 else dy

        afkast_dele = [v for v in (overskudsvaekst_pct, buyback_pct, udbytte_pct) if v is not None]
        forventet_afkast_pct = sum(afkast_dele) if afkast_dele else None
        fase2_score = None
        if forventet_afkast_pct is not None:
            # Kalibrering (min egen, ikke bogens): 0% → 0 point, ~10% → 50
            # point, 20%+ → 100 point. Groft justeret efter brugerens
            # eksempel, hvor bogen selv sammenligner med "statsobligationer
            # (~2-3%)" og "gennemsnitligt aktieafkast (~7-10%)".
            fase2_score = max(0.0, min(100.0, forventet_afkast_pct * 5.0))
            if forventet_afkast_pct >= 10:
                triggers.append(
                    f"Attraktivt forventet afkast ({forventet_afkast_pct:+.1f}%: "
                    f"{overskudsvaekst_pct or 0:.1f}% overskudsvækst + "
                    f"{buyback_pct or 0:.1f}% tilbagekøb + {udbytte_pct or 0:.1f}% udbytte)"
                )

        # --- Fase 3: Kvalitetspoint (fortolkning, se docstring) ---
        insiders = info.get("heldPercentInsiders")
        institutions = info.get("heldPercentInstitutions")
        debt_to_equity = fundamental.get("debt_to_equity") if fa_ok else info.get("debtToEquity")
        payout_ratio = info.get("payoutRatio")
        beta = info.get("beta")

        fase3_scores = []
        if insiders is not None:
            fase3_scores.append(max(0.0, min(100.0, insiders * 100.0 / 10.0 * 100.0)))
        if institutions is not None:
            fase3_scores.append(max(0.0, min(100.0, institutions * 100.0)))
        if debt_to_equity is not None:
            fase3_scores.append(max(0.0, min(100.0, 100.0 - debt_to_equity / 2.0)))
        if payout_ratio is not None:
            p = payout_ratio * 100.0
            # En "sund" udlodningsgrad (60-100%) ses som kvalitetstegn — en
            # for lav (<60%) eller for høj (>100%, dvs. udbetaler mere end
            # overskuddet) trækker ned.
            if 60.0 <= p <= 100.0:
                fase3_scores.append(100.0)
            elif p < 60.0:
                fase3_scores.append(max(0.0, p / 60.0 * 100.0))
            else:
                fase3_scores.append(max(0.0, 100.0 - (p - 100.0)))
        if beta is not None:
            fase3_scores.append(max(0.0, min(100.0, 100.0 - abs(beta - 1.0) * 50.0)))
        fase3_score = sum(fase3_scores) / len(fase3_scores) if fase3_scores else None
        if fase3_score is not None and fase3_score >= 65:
            triggers.append(f"Solidt kvalitetsbillede (Fase 3: {fase3_score:.0f}/100)")

        component_scores = [s for s in (fase1_score, fase2_score, fase3_score) if s is not None]
        aktieguld_score = sum(component_scores) / len(component_scores) if component_scores else None

        return {
            "ok": aktieguld_score is not None,
            "reason": None if aktieguld_score is not None else "ingen af de tre faser kunne beregnes (mangler nøgletal)",
            "fase1_score": fase1_score,
            "fase2_score": fase2_score,
            "fase2_forventet_afkast_pct": forventet_afkast_pct,
            "fase2_overskudsvaekst_pct": overskudsvaekst_pct,
            "fase2_buyback_pct": buyback_pct,
            "fase2_udbytte_pct": udbytte_pct,
            "fase3_score": fase3_score,
            "aktieguld_score": aktieguld_score,
            "aktieguld_triggers": triggers,
        }
    except Exception as e:
        return {"ok": False, "reason": f"kunne ikke beregne Aktieguld-point: {e}"}


# ============================================================================
# --- Sektor-rotation (info-badge — påvirker IKKE komposit-scoren) ---
# ============================================================================
#
# Tilføjet efter ønske fra bruger (Jacobi, 2026-09-17), afklaret via
# opklarende spørgsmål: vises KUN som ekstra info/trigger i UI'en, indgår
# ikke i komposit-scoren eller nogen af de fire vægtede faktorer.
#
# Metode (bekræftet af bruger): en sektors "rotation" måles som dens
# RELATIVE styrke — sektor-ETF'ens kursafkast de seneste ~3 måneder minus
# verdensindekset (BENCHMARK = ACWI) i samme periode. Positiv = sektoren er
# "i medvind" (slår markedet), negativ = "i modvind" (halter efter).
#
# Bruger har både amerikanske og danske/europæiske aktier. Der findes ingen
# separate danske sektor-ETF'er, så vi bruger aktiens globale GICS-sektor
# (Yahoo Finances "sector"-felt, fx "Technology") som proxy og matcher den
# til den tilsvarende amerikanske sektor-ETF (SPDR Select Sector-serien).
# Dette er en tilnærmelse — sektor-cyklusser hænger langt fra perfekt sammen
# på tværs af markeder — men bruger har accepteret dette frem for slet ingen
# sektor-info for hovedparten af hans aktier.

SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Financial Services": "XLF",
    "Healthcare": "XLV",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Consumer Cyclical": "XLY",
    "Consumer Defensive": "XLP",
    "Utilities": "XLU",
    "Basic Materials": "XLB",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}

SECTOR_ROTATION_LOOKBACK_DAYS = 63   # ~3 måneders handelsdage (bekræftet af bruger)
SECTOR_ROTATION_THRESHOLD_PCT = 3.0  # +/- procentpoint relativ styrke for "medvind"/"modvind"


def _period_return_pct(ticker: str, lookback_days: int):
    """Kursafkast i procent de seneste `lookback_days` handelsdage. Returnerer
    None ved manglende/utilstrækkelig historik i stedet for at kaste en fejl
    — kaldende kode springer så bare den pågældende sektor/ticker over."""
    df, err = fetch_ticker_df(ticker, period="6mo")
    if df is None:
        return None
    close = df["Close"].dropna()
    if len(close) < lookback_days + 5:
        return None
    last_price = float(close.iloc[-1])
    prior_price = float(close.iloc[-(lookback_days + 1)])
    if not prior_price:
        return None
    return (last_price / prior_price - 1.0) * 100.0


def get_sector_rotation_map(lookback_days: int = SECTOR_ROTATION_LOOKBACK_DAYS) -> dict:
    """Beregner relativ styrke for alle 11 GICS-sektorer ift. verdensindekset
    ÉN GANG (ikke pr. aktie) — kaldende lag (app.py) bør cache resultatet
    (fx 1 times TTL), da 3-måneders sektorafkast ikke ændrer sig fra
    minut til minut. Bruges kun til info-badges, se get_sector_signal()."""
    benchmark_return = _period_return_pct(BENCHMARK, lookback_days)
    result = {"ok": benchmark_return is not None, "benchmark_return_pct": benchmark_return, "sectors": {}}
    if benchmark_return is None:
        return result
    for sector_name, etf in SECTOR_ETF_MAP.items():
        etf_return = _period_return_pct(etf, lookback_days)
        if etf_return is None:
            continue
        result["sectors"][sector_name] = {
            "etf": etf,
            "etf_return_pct": etf_return,
            "relative_strength_pct": etf_return - benchmark_return,
        }
    return result


def get_sector_signal(sector_name, sector_map: dict):
    """Slår en akties GICS-sektor op i det forudberegnede sector_map og
    klassificerer den som medvind/modvind/neutral. Returnerer None hvis
    sektoren er ukendt eller data mangler — UI'en udelader så bare badge'en
    i stedet for at vise en fejl (samme princip som Aktieguld-fallback)."""
    if not sector_name or not sector_map or not sector_map.get("ok"):
        return None
    info = sector_map.get("sectors", {}).get(sector_name)
    if not info:
        return None
    rs = info["relative_strength_pct"]
    if rs >= SECTOR_ROTATION_THRESHOLD_PCT:
        retning = "medvind"
    elif rs <= -SECTOR_ROTATION_THRESHOLD_PCT:
        retning = "modvind"
    else:
        retning = "neutral"
    return {
        "sector": sector_name,
        "etf": info["etf"],
        "relative_strength_pct": rs,
        "etf_return_pct": info["etf_return_pct"],
        "benchmark_return_pct": sector_map.get("benchmark_return_pct"),
        "retning": retning,
    }


def get_stocktwits_data(ticker):
    try:
        import requests
    except ImportError:
        return {"ok": False, "reason": "requests ikke installeret"}
    symbol = ticker.split(".")[0]
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
    try:
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (Mine Aktier; personligt værktøj)"}, timeout=8)
        if resp.status_code != 200:
            return {"ok": False, "reason": f"HTTP {resp.status_code}"}
        data = resp.json()
        messages = data.get("messages") or []
        tagged = [m for m in messages if (m.get("entities") or {}).get("sentiment") is not None]
        bullish = sum(1 for m in tagged if (m["entities"]["sentiment"] or {}).get("basic") == "Bullish")
        sample = len(tagged)
        bullish_ratio = (bullish / sample * 100.0) if sample else None
        stocktwits_score = None
        if bullish_ratio is not None:
            weight = min(sample / 10.0, 1.0)
            stocktwits_score = weight * bullish_ratio + (1 - weight) * 50.0
        return {"ok": True, "bullish_ratio": bullish_ratio, "sample": sample, "stocktwits_score": stocktwits_score}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _get_secret(name):
    try:
        import streamlit as st
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.environ.get(name)


def get_reddit_data(ticker, cfg, company_name=None):
    try:
        import praw
    except ImportError:
        return {"ok": False, "reason": "praw ikke installeret"}
    client_id, client_secret = _get_secret("REDDIT_CLIENT_ID"), _get_secret("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return {"ok": False, "reason": "Reddit API-nøgler ikke sat op"}
    try:
        reddit = praw.Reddit(client_id=client_id, client_secret=client_secret,
                              user_agent="mine_aktier/1.0")
        reddit.read_only = True
        terms = {ticker.split(".")[0]}
        if company_name:
            terms.add(company_name)
        mentions = []
        for sub in cfg.reddit_subreddits:
            try:
                for term in terms:
                    for post in reddit.subreddit(sub).search(term, time_filter=cfg.reddit_time_filter,
                                                               limit=cfg.reddit_search_limit):
                        mentions.append(post.score)
            except Exception:
                continue
        return {"ok": True, "mention_count": len(mentions)}
    except Exception as e:
        return {"ok": False, "reason": str(e)}


def _reddit_score(reddit: dict):
    """Absolut buzz-score (0-100) ud fra antal omtaler i perioden.

    I screener.py rangeres Reddit-omtale relativt til de andre kandidater i
    samme kørsel (percentil). Her analyserer vi kun én ticker ad gangen, så
    der er ingen sammenligningsgruppe — vi bruger i stedet en fast skala:
    0 omtaler = neutral (50), 20+ omtaler på en uge = maks buzz (100).
    """
    if not reddit.get("ok"):
        return None
    n = reddit.get("mention_count", 0)
    if n <= 0:
        return 50.0
    return 50.0 + min(n / 20.0, 1.0) * 50.0


def compute_upside(ta, analyst):
    """Upside baseres UDELUKKENDE på analytiker-konsensus (gennemsnitligt
    kursmål). Rettet efter brugerfeedback (Jacobi, 2026-08-28): der var
    tidligere en fallback til et "teknisk estimat" (afstand op til den
    højeste kurs i historikken) når ingen analytikere dækkede aktien — det
    var misvisende, fordi det ikke er en fremadskuende vurdering af nogen
    art, bare en afstandsmåling, men blev vist under samme "Upside"-label
    som den rigtige analytikervurdering. Nu vises INTET upside-tal, når der
    ikke er analytikerdækning — GUI'en (app.py) udelader hele
    "Upside: ..."-linjen i det tilfælde, i stedet for et misvisende tal."""
    last_price = ta["last_price"]
    if analyst.get("ok") and analyst.get("target_mean") and last_price:
        n = analyst.get("num_analysts")
        kilde = f"Analytiker-konsensus ({n} analytikere)" if n else "Analytiker-konsensus"
        return (analyst["target_mean"] / last_price - 1.0) * 100.0, kilde
    return None, "Ingen analytikerdækning"


def popularity_score(analyst, stocktwits, reddit):
    comps, weights = [], []
    if analyst.get("ok") and analyst.get("analyst_score") is not None:
        comps.append(analyst["analyst_score"]); weights.append(0.5)
    if stocktwits.get("ok") and stocktwits.get("stocktwits_score") is not None:
        comps.append(stocktwits["stocktwits_score"]); weights.append(0.3)
    r_score = _reddit_score(reddit)
    if r_score is not None:
        comps.append(r_score); weights.append(0.2)
    if not comps:
        return None
    return sum(c * w for c, w in zip(comps, weights)) / sum(weights)


def combine_composite_score(ta, long_term, fundamental, popularity_val, aktieguld, cfg):
    """Slår teknisk (kort+lang bane), fundamental, popularitets- og
    Aktieguld-score sammen til den endelige komposit-rating (0-100).
    Standardvægtning: teknisk 24% / fundamental 20% / popularitet 36% /
    Aktieguld 20% (jf. bruger, 2026-09 — Aktieguld tilføjet som 4. faktor,
    de tre oprindelige nedskaleret proportionalt, se Config). Popularitet
    vægtes stadig tungest blandt de fire, fordi tiltro/opmærksomhed har stor
    effekt på om et setup rent faktisk spiller ud (jf. bruger). Manglende
    data på en faktor giver en neutral 50 i stedet for at straffe aktien for
    manglende dækning."""
    if long_term.get("ok"):
        ta_combined = 0.6 * ta["ta_score"] + 0.4 * long_term["long_term_score"]
    else:
        ta_combined = ta["ta_score"]
    fa_score = fundamental.get("fundamental_score") if fundamental.get("ok") else None
    fa_score = fa_score if fa_score is not None else 50.0
    pop_score = popularity_val if popularity_val is not None else 50.0
    ag_score = aktieguld.get("aktieguld_score") if aktieguld and aktieguld.get("ok") else None
    ag_score = ag_score if ag_score is not None else 50.0
    return (cfg.weight_ta * ta_combined + cfg.weight_fundamental * fa_score
            + cfg.weight_popularity * pop_score + cfg.weight_aktieguld * ag_score)


def compute_recommendation(ta, long_term, fundamental, stocktwits, plan, composite_score) -> dict:
    """Samler alle sælg-relaterede signaler til én klar handling — KØB, HOLD
    eller SÆLG — plus begrundelserne bag. Regelbaseret forslag til videre
    research, ikke en ordre eller garanti. Bruges både på watchlist-kort
    (til at vurdere om noget du allerede følger bør sælges), i Top 10 og i
    det automatiske daglige tjek (alert_check.py)."""
    sell_score = ta.get("sell_score", 0)
    sell_triggers = list(ta.get("sell_triggers", []))

    stop_breached = plan.get("stop_price") is not None and ta["last_price"] < plan["stop_price"]
    if stop_breached:
        sell_score += 30
        sell_triggers.append(
            f"Kurs ({ta['last_price']:.2f}) er under stop-niveauet ({plan['stop_price']:.2f})"
        )

    if long_term.get("ok") and long_term.get("momentum_12m") is not None and long_term["momentum_12m"] < -10:
        sell_score += 15
        sell_triggers.append(f"Negativt 12-måneders momentum ({long_term['momentum_12m']:+.1f}%)")

    if fundamental.get("ok") and fundamental.get("fundamental_score") is not None \
            and fundamental["fundamental_score"] < 30:
        sell_score += 10
        sell_triggers.append("Svage fundamentale nøgletal")

    if stocktwits.get("ok") and stocktwits.get("bullish_ratio") is not None \
            and stocktwits["bullish_ratio"] < 35:
        sell_score += 10
        sell_triggers.append(f"Overvejende bearish StockTwits-sentiment ({stocktwits['bullish_ratio']:.0f}% bullish)")

    sell_score = min(sell_score, 100)

    if sell_score >= 50 or not ta.get("trend_ok", True) or stop_breached:
        action = "SÆLG"
    elif composite_score >= 60 and sell_score < 20:
        action = "KØB"
    else:
        action = "HOLD"

    return {"action": action, "sell_score": sell_score, "sell_triggers": sell_triggers}


def compute_trade_plan(df, last_price: float, cfg) -> dict:
    """Handelsforslag: stop-loss + et indgangsvindue og en tidsbaseret
    'stopud dato' (maks. holdeperiode, hvis kursen hverken rammer stop-loss
    eller går tydeligt i vores favør).

    Stop-loss-regel ("loft over tab"): det tekniske niveau (seneste
    kursbund eller 50-dages glidende gennemsnit, alt efter hvad der er
    lavest) bruges, MEN tabet må aldrig blive større end
    `cfg.stop_loss_max_pct` (default 10%) fra indgangskursen — er det
    tekniske niveau dybere end det, bruges -10%-loftet i stedet. Findes
    intet teknisk niveau under kursen, bruges -10%-loftet alene.

    Dette er et forslag til videre research — ikke en ordre eller en
    garanti, og det er ikke selv backtestet (i modsætning til
    stop_loss_pct-reglen i backtesten)."""
    close = df["Close"].dropna()
    low_col = df["Low"].dropna() if "Low" in df.columns else close

    sma50 = float(close.rolling(50, min_periods=50).mean().iloc[-1]) if len(close) >= 50 else None
    swing_low = float(low_col.tail(20).min()) if len(low_col) >= 20 else None
    technical_levels = [v for v in (sma50, swing_low) if v is not None and v < last_price]
    technical_stop = min(technical_levels) * 0.99 if technical_levels else None  # lille buffer under niveauet

    max_loss_stop = last_price * (1.0 - cfg.stop_loss_max_pct)
    loft_pct_txt = f"{cfg.stop_loss_max_pct * 100:.0f}%"

    if technical_stop is not None and technical_stop >= max_loss_stop:
        # Det tekniske niveau indebærer allerede et tab på højst stop_loss_max_pct — brug det.
        stop_price = technical_stop
        stop_kilde = ("seneste kursbund" if (swing_low in technical_levels and swing_low <= (sma50 or float("inf")))
                      else "50-dages glidende gennemsnit")
    elif technical_stop is not None:
        # Det tekniske niveau ligger dybere end -X% — loftet strammer stoppet.
        stop_price = max_loss_stop
        stop_kilde = f"−{loft_pct_txt}-loft (strammere end det tekniske niveau)"
    else:
        stop_price = max_loss_stop
        stop_kilde = f"−{loft_pct_txt}-loft (intet teknisk niveau fundet under kursen)"

    stop_pct = (stop_price / last_price - 1.0) * 100.0

    today = np.datetime64(datetime.date.today())
    entry_deadline = str(np.busday_offset(today, cfg.entry_window_days, roll="forward"))
    stop_out_date = str(np.busday_offset(today, cfg.swing_hold_days, roll="forward"))

    return {
        "entry_price": last_price,
        "entry_deadline": entry_deadline,
        "stop_price": stop_price,
        "stop_pct": stop_pct,
        "stop_kilde": stop_kilde,
        "stop_out_date": stop_out_date,
    }


def analyze_ticker(ticker: str):
    """Fuld analyse af én ticker: teknisk (kort+lang bane), fundamental og
    analytiker/sentiment-score, komposit-rating, upside, handelsplan og en
    KØB/HOLD/SÆLG-anbefaling."""
    cfg = Config()
    df, err = fetch_ticker_df(ticker)
    if df is None:
        return {"ok": False, "reason": err or "ingen data"}
    ta = compute_technical_signals(df, cfg)
    if not ta.get("ok"):
        return {"ok": False, "reason": ta.get("reason", "utilstrækkelig historik")}
    long_term = compute_long_term_signals(df, cfg)
    fundamental = get_fundamental_data(ticker)
    analyst = get_analyst_data(ticker)
    stocktwits = get_stocktwits_data(ticker)
    reddit = get_reddit_data(ticker, cfg, company_name=analyst.get("short_name"))
    pop = popularity_score(analyst, stocktwits, reddit)
    aktieguld = get_aktieguld_data(ticker, fundamental)
    composite = combine_composite_score(ta, long_term, fundamental, pop, aktieguld, cfg)
    upside, upside_kilde = compute_upside(ta, analyst)
    plan = compute_trade_plan(df, ta["last_price"], cfg)
    recommendation = compute_recommendation(ta, long_term, fundamental, stocktwits, plan, composite)
    return {
        "ok": True, "ta": ta, "long_term": long_term, "fundamental": fundamental,
        "analyst": analyst, "stocktwits": stocktwits, "reddit": reddit,
        "aktieguld": aktieguld,
        "composite_score": composite, "popularity_score": pop,
        "upside_pct": upside, "upside_kilde": upside_kilde,
        "plan": plan, "recommendation": recommendation,
        "short_name": analyst.get("short_name"),
    }


def find_top_candidates(top_n: int = 10):
    """Scanner hele det kuraterede univers (~93 aktier) og returnerer de
    `top_n` bedst rangerede kandidater efter samme TA + analytiker/sentiment-
    komposit-score som resten af værktøjet. To trin, som i kandidat-
    screeneren:
    1) hurtig teknisk scanning af hele universet (ingen ekstra API-kald),
    2) analytiker-/sentiment-opslag kun for de bedste tekniske kandidater —
    for at holde antal netværkskald og køretid nede.

    Returnerer (kandidater, stats) hvor stats fortæller hvor mange tickere
    der blev sprunget over og hvorfor, så intet forsvinder i stilhed."""
    cfg = Config()
    universe = [t for t in full_universe() if t != BENCHMARK]

    # --- Trin 1: teknisk scanning af hele universet ---
    # Kort høflighedspause mellem kaldene (rettet — se BACKLOG.md #3): uden
    # den kan mange hurtige, sekventielle kald til Yahoo Finance udløse
    # midlertidig rate-limitering, som kan få langt flere tickere end
    # normalt til at fejle datahentningen — og dermed give et kunstigt lille
    # antal kandidater i Top 10, uafhængigt af om markedet reelt er svagt.
    ta_pass = []
    skipped_data, skipped_trend = 0, 0
    for i, ticker in enumerate(universe):
        df, err = fetch_ticker_df(ticker, period="5y")
        if df is None:
            skipped_data += 1
        else:
            ta = compute_technical_signals(df, cfg)
            if not ta.get("ok") or not ta.get("trend_ok"):
                skipped_trend += 1
            else:
                ta_pass.append({"ticker": ticker, "ta": ta})
        if i < len(universe) - 1:
            time.sleep(cfg.scan_request_delay_sec)

    ta_pass.sort(key=lambda r: r["ta"]["ta_score"], reverse=True)
    shortlist = ta_pass[: cfg.screener_shortlist_size]

    # --- Trin 2: analytiker/sentiment + handelsplan for shortlisten ---
    results = []
    for i, row in enumerate(shortlist):
        ticker, ta = row["ticker"], row["ta"]
        df, err = fetch_ticker_df(ticker, period="5y")
        if df is None:
            continue
        long_term = compute_long_term_signals(df, cfg)
        fundamental = get_fundamental_data(ticker)
        analyst = get_analyst_data(ticker)
        stocktwits = get_stocktwits_data(ticker)
        reddit = get_reddit_data(ticker, cfg, company_name=analyst.get("short_name"))
        pop = popularity_score(analyst, stocktwits, reddit)
        aktieguld = get_aktieguld_data(ticker, fundamental)
        composite = combine_composite_score(ta, long_term, fundamental, pop, aktieguld, cfg)
        upside, upside_kilde = compute_upside(ta, analyst)
        plan = compute_trade_plan(df, ta["last_price"], cfg)
        recommendation = compute_recommendation(ta, long_term, fundamental, stocktwits, plan, composite)
        meta = get_ticker_meta(ticker)
        results.append({
            "ticker": ticker, "region": region_of(ticker), "ta": ta,
            "long_term": long_term, "fundamental": fundamental,
            "analyst": analyst, "stocktwits": stocktwits, "reddit": reddit,
            "aktieguld": aktieguld,
            "composite_score": composite, "popularity_score": pop,
            "upside_pct": upside, "upside_kilde": upside_kilde,
            "short_name": analyst.get("short_name") or ticker, "plan": plan, "meta": meta,
            "recommendation": recommendation,
        })
        if i < len(shortlist) - 1:
            time.sleep(cfg.sentiment_request_delay_sec)

    results.sort(key=lambda r: r["composite_score"], reverse=True)
    universe_size = len(universe)
    stats = {
        "universe_size": universe_size, "skipped_data": skipped_data,
        "skipped_trend": skipped_trend, "shortlist_size": len(shortlist),
        # Rettet — se BACKLOG.md #3: signalerer til GUI'en at usædvanligt
        # mange tickere fejlede datahentning, som tyder på midlertidig
        # rate-limitering snarere end et roligt marked.
        "high_skip_rate": (universe_size > 0
                            and (skipped_data / universe_size) > cfg.scan_skipped_data_warn_ratio),
    }
    return results[:top_n], stats


# ============================================================================
# --- Statisk watchlist til det automatiske daglige tjek (alert_check.py) ---
# ============================================================================

STATIC_WATCHLIST_FILE = "watchlist_tickers.txt"


def load_static_watchlist(path: str = STATIC_WATCHLIST_FILE):
    """Læser den GitHub-forvaltede ticker-liste som bruges af det
    automatiske daglige tjek. Én ticker pr. linje; tomme linjer og linjer
    der starter med '#' ignoreres. Adskilt fra app'ens egen watchlist
    (watchlist.json), som er midlertidig og kun lever i Streamlit-appen."""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(line.upper())
    return out

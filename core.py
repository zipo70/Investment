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
    # --- Komposit-vægtning (teknisk / fundamental / popularitet) ---
    weight_ta = 0.30
    weight_fundamental = 0.25
    weight_popularity = 0.45   # vægtet tungest — populæritet/tiltro har stor effekt (jf. bruger)
    long_term_lookback_days = 252   # ~12 måneder, til langsigtet momentum-trigger
    period_high_proximity_pct = 5.0  # "tæt på flerårs-højeste" = inden for X% af perioden-høj
    reddit_subreddits = ["stocks", "investing", "wallstreetbets"]
    reddit_time_filter = "week"
    reddit_search_limit = 15
    min_history_days = 220
    screener_shortlist_size = 20       # kun de N bedste TA-kandidater går videre
                                        # til analytiker-/sentiment-opslag
    sentiment_request_delay_sec = 1.0  # høflighedspause mellem API-kald i Top 10-scan
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
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance ikke installeret"
    last_err = None
    for attempt in range(3):
        try:
            df = yf.download(ticker, period=period, auto_adjust=False, progress=False, threads=False)
            if df is None or df.empty:
                raise ValueError("tom respons")
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.rename(columns={"Adj Close": "AdjClose"})
            return df, None
        except Exception as e:
            last_err = str(e)
            time.sleep(1.0 * (attempt + 1))
    return None, last_err


def resolve_ticker(query: str):
    """Slår et frit navn/ticker op via Yahoo Finances (uofficielle) søge-API,
    så man kan skrive fx "novo" i stedet for at kende den præcise Yahoo-
    ticker-syntaks (NOVO-B.CO). Returnerer (symbol, visningsnavn, børs) —
    eller (None, None, None) hvis søgningen fejler eller intet findes, så
    kaldende kode altid kan falde tilbage til at bruge inputtet som ticker
    direkte."""
    try:
        import requests
    except ImportError:
        return None, None, None
    try:
        resp = requests.get(
            "https://query2.finance.yahoo.com/v1/finance/search",
            params={"q": query, "quotesCount": 6, "newsCount": 0, "lang": "en-US"},
            headers={"User-Agent": "Mozilla/5.0 (Mine Aktier; personligt værktøj)"},
            timeout=6,
        )
        if resp.status_code != 200:
            return None, None, None
        quotes = [q for q in (resp.json().get("quotes") or []) if q.get("symbol")]
        equities = [q for q in quotes if q.get("quoteType") == "EQUITY"] or quotes
        if not equities:
            return None, None, None
        top = equities[0]
        name = top.get("shortname") or top.get("longname") or top["symbol"]
        exchange = top.get("exchDisp") or top.get("exchange")
        return top["symbol"], name, exchange
    except Exception:
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
        try:
            info = yf.Ticker(ticker).info or {}
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

        fundamental_score = (
            sum(s * w for s, w in zip(subscores, weights)) / sum(weights) if subscores else None
        )

        return {
            "ok": True, "pe": pe, "peg": peg, "profit_margin": profit_margin,
            "revenue_growth": revenue_growth, "earnings_growth": earnings_growth,
            "roe": roe, "debt_to_equity": debt_to_equity,
            "fundamental_score": fundamental_score, "fundamental_triggers": triggers,
        }
    except Exception as e:
        return {"ok": False, "reason": f"kunne ikke hente nøgletal: {e}"}


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
    last_price = ta["last_price"]
    if analyst.get("ok") and analyst.get("target_mean") and last_price:
        n = analyst.get("num_analysts")
        kilde = f"Analytiker-konsensus ({n} analytikere)" if n else "Analytiker-konsensus"
        return (analyst["target_mean"] / last_price - 1.0) * 100.0, kilde
    high_252 = ta.get("high_252w")
    if high_252 and last_price and last_price < high_252:
        return (high_252 / last_price - 1.0) * 100.0, "Teknisk estimat (afstand til 52-ugers højeste)"
    return None, "Intet analytikerdækning og intet klart teknisk mål"


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


def combine_composite_score(ta, long_term, fundamental, popularity_val, cfg):
    """Slår teknisk (kort+lang bane), fundamental og popularitets-score sammen
    til den endelige komposit-rating (0-100). Standardvægtning: teknisk 30% /
    fundamental 25% / popularitet 45% — popularitet vægtes tungest, fordi
    tiltro/opmærksomhed har stor effekt på om et setup rent faktisk spiller
    ud (jf. bruger). Manglende data på en faktor giver en neutral 50 i
    stedet for at straffe aktien for manglende dækning."""
    if long_term.get("ok"):
        ta_combined = 0.6 * ta["ta_score"] + 0.4 * long_term["long_term_score"]
    else:
        ta_combined = ta["ta_score"]
    fa_score = fundamental.get("fundamental_score") if fundamental.get("ok") else None
    fa_score = fa_score if fa_score is not None else 50.0
    pop_score = popularity_val if popularity_val is not None else 50.0
    return cfg.weight_ta * ta_combined + cfg.weight_fundamental * fa_score + cfg.weight_popularity * pop_score


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
    composite = combine_composite_score(ta, long_term, fundamental, pop, cfg)
    upside, upside_kilde = compute_upside(ta, analyst)
    plan = compute_trade_plan(df, ta["last_price"], cfg)
    recommendation = compute_recommendation(ta, long_term, fundamental, stocktwits, plan, composite)
    return {
        "ok": True, "ta": ta, "long_term": long_term, "fundamental": fundamental,
        "analyst": analyst, "stocktwits": stocktwits, "reddit": reddit,
        "composite_score": composite, "upside_pct": upside, "upside_kilde": upside_kilde,
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
    ta_pass = []
    skipped_data, skipped_trend = 0, 0
    for ticker in universe:
        df, err = fetch_ticker_df(ticker, period="5y")
        if df is None:
            skipped_data += 1
            continue
        ta = compute_technical_signals(df, cfg)
        if not ta.get("ok") or not ta.get("trend_ok"):
            skipped_trend += 1
            continue
        ta_pass.append({"ticker": ticker, "ta": ta})

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
        composite = combine_composite_score(ta, long_term, fundamental, pop, cfg)
        upside, upside_kilde = compute_upside(ta, analyst)
        plan = compute_trade_plan(df, ta["last_price"], cfg)
        recommendation = compute_recommendation(ta, long_term, fundamental, stocktwits, plan, composite)
        meta = get_ticker_meta(ticker)
        results.append({
            "ticker": ticker, "region": region_of(ticker), "ta": ta,
            "long_term": long_term, "fundamental": fundamental,
            "analyst": analyst, "stocktwits": stocktwits, "reddit": reddit,
            "composite_score": composite, "upside_pct": upside, "upside_kilde": upside_kilde,
            "short_name": analyst.get("short_name") or ticker, "plan": plan, "meta": meta,
            "recommendation": recommendation,
        })
        if i < len(shortlist) - 1:
            time.sleep(cfg.sentiment_request_delay_sec)

    results.sort(key=lambda r: r["composite_score"], reverse=True)
    stats = {
        "universe_size": len(universe), "skipped_data": skipped_data,
        "skipped_trend": skipped_trend, "shortlist_size": len(shortlist),
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

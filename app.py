"""
Mine Aktier — mobil-app-lignende GUI til watchlist + rating.

Selvstændig fil (samme "alt-i-én" princip som swing_screener_single_cell.py):
alle nødvendige funktioner fra universe.py/config.py/data_fetch.py/
technicals.py/sentiment.py er kopieret ind her, så filen kan lægges direkte i
GitHub-repoet og deployes på Streamlit Community Cloud uden mappestruktur.

Funktioner:
- Watchlist: tilføj/fjern aktier (Yahoo Finance-tickere).
- Hvert kort viser: navn, seneste kurs, dagens ændring, en mini-graf
  (som "Værdipapirer"-appen), og — efter du trykker "Analysér" — en
  komposit-rating (0-100) og et upside-estimat, genbrugt fra
  kandidat-screenerens logik (TA + analytiker + sentiment).
- AI chat-fane: UI-skelettet er der, men svarene er statiske indtil en
  rigtig API-nøgle kobles på (bevidst fravalgt i første version — se
  README.md).

Kør lokalt: streamlit run app.py
Deploy: se README.md, afsnittet "Mine Aktier (mobil-app)".
"""

import os
import json
import time

import numpy as np
import pandas as pd
import streamlit as st

# ============================================================================
# --- Config (fra config.py, kun de felter appen bruger) ---
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
    screener_ta_weight = 0.5
    screener_sentiment_weight = 0.5
    reddit_subreddits = ["stocks", "investing", "wallstreetbets"]
    reddit_time_filter = "week"
    reddit_search_limit = 15
    min_history_days = 220


# ============================================================================
# --- Datahentning (fra data_fetch.py, forenklet til én ticker ad gangen) ---
# ============================================================================

def fetch_ticker_df(ticker: str, period: str = "2y"):
    try:
        import yfinance as yf
    except ImportError:
        return None, "yfinance ikke installeret"
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


# ============================================================================
# --- Tekniske signaler (fra technicals.py) ---
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

    rsi_series = rsi(close, cfg.rsi_window)
    rsi_cross_up = (rsi_series > 30) & (rsi_series.shift(1) <= 30)
    rsi_hit, rsi_days_ago = _recent_event(rsi_cross_up, cfg.momentum_shift_lookback_days)

    macd_hist = macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    macd_flip_up = (macd_hist > 0) & (macd_hist.shift(1) <= 0)
    macd_hit, macd_days_ago = _recent_event(macd_flip_up, cfg.momentum_shift_lookback_days)

    momentum_hit = rsi_hit or macd_hit

    avg_vol_20 = volume.rolling(cfg.volume_spike_window, min_periods=cfg.volume_spike_window).mean()
    last_vol = float(volume.iloc[-1]) if not volume.empty else float("nan")
    last_avg_vol = float(avg_vol_20.iloc[-1]) if not avg_vol_20.empty else float("nan")
    vol_ratio = (last_vol / last_avg_vol) if last_avg_vol and not np.isnan(last_avg_vol) and last_avg_vol > 0 else float("nan")
    vol_spike = bool(vol_ratio and not np.isnan(vol_ratio) and vol_ratio >= cfg.volume_spike_threshold)

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

    return {
        "ok": True, "last_price": last_price, "trend_ok": bool(trend_ok),
        "high_252w": float(close.tail(252).max()), "ta_score": min(score, 100),
        "ta_triggers": triggers,
    }


# ============================================================================
# --- Analytiker/sentiment (fra sentiment.py) ---
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


@st.cache_data(ttl=900, show_spinner=False)
def analyze_ticker(ticker: str):
    """Fuld analyse af én ticker: TA-score, analytiker/sentiment, komposit-rating, upside.
    Cachet 15 min. så gentagne klik ikke spammer Yahoo Finance/StockTwits."""
    cfg = Config()
    df, err = fetch_ticker_df(ticker)
    if df is None:
        return {"ok": False, "reason": err or "ingen data"}
    ta = compute_technical_signals(df, cfg)
    if not ta.get("ok"):
        return {"ok": False, "reason": ta.get("reason", "utilstrækkelig historik")}
    analyst = get_analyst_data(ticker)
    stocktwits = get_stocktwits_data(ticker)
    reddit = get_reddit_data(ticker, cfg, company_name=analyst.get("short_name"))
    pop = popularity_score(analyst, stocktwits, reddit)
    sent_score = pop if pop is not None else 50.0
    composite = cfg.screener_ta_weight * ta["ta_score"] + cfg.screener_sentiment_weight * sent_score
    upside, upside_kilde = compute_upside(ta, analyst)
    return {
        "ok": True, "ta": ta, "analyst": analyst, "stocktwits": stocktwits, "reddit": reddit,
        "composite_score": composite, "upside_pct": upside, "upside_kilde": upside_kilde,
        "short_name": analyst.get("short_name"),
    }


# ============================================================================
# --- Watchlist-persistens (simpel JSON-fil; se README om begrænsninger) ---
# ============================================================================

WATCHLIST_FILE = "watchlist.json"


def load_watchlist():
    if os.path.exists(WATCHLIST_FILE):
        try:
            return json.load(open(WATCHLIST_FILE))
        except Exception:
            return []
    return []


def save_watchlist(wl):
    try:
        json.dump(wl, open(WATCHLIST_FILE, "w"))
    except Exception:
        pass


# ============================================================================
# --- GUI ---
# ============================================================================

st.set_page_config(page_title="Mine Aktier", page_icon="📈", layout="centered")

st.markdown("""
<style>
  .stock-card {border:1px solid #e1e0d9; border-radius:12px; padding:12px 16px; margin-bottom:10px; background:#fcfcfb;}
  .stock-top {display:flex; justify-content:space-between; align-items:baseline;}
  .stock-name {font-size:0.78rem; color:#898781;}
  .stock-price {font-size:1.1rem; font-weight:600;}
  .change-pos {color:#0ca30c; font-weight:600;}
  .change-neg {color:#d03b3b; font-weight:600;}
  .rating-badge {display:inline-block; padding:2px 10px; border-radius:999px; font-size:0.78rem; font-weight:600; margin-top:6px;}
  .rating-high {background:#e6f6e6; color:#0ca30c;}
  .rating-mid {background:#fff4e0; color:#b9770e;}
  .rating-low {background:#fdeaea; color:#d03b3b;}
</style>
""", unsafe_allow_html=True)

if "watchlist" not in st.session_state:
    st.session_state.watchlist = load_watchlist()
if "chat_history" not in st.session_state:
    st.session_state.chat_history = []

st.title("📈 Mine Aktier")

tab_watch, tab_chat = st.tabs(["Watchlist", "AI Chat"])

# --- Watchlist-fane ---
with tab_watch:
    with st.form("add_form", clear_on_submit=True):
        c1, c2 = st.columns([3, 1])
        new_ticker = c1.text_input(
            "Tilføj aktie", label_visibility="collapsed",
            placeholder="Ticker, fx AAPL, NOVO-B.CO, ASML.AS",
        )
        add_clicked = c2.form_submit_button("Tilføj", use_container_width=True)
    if add_clicked and new_ticker.strip():
        t = new_ticker.strip().upper()
        if t not in st.session_state.watchlist:
            st.session_state.watchlist.append(t)
            save_watchlist(st.session_state.watchlist)
        st.rerun()

    if not st.session_state.watchlist:
        st.info("Din watchlist er tom — tilføj en aktie ovenfor for at komme i gang.")
    else:
        if st.button("🔄 Analysér watchlist", use_container_width=True):
            progress = st.progress(0.0, text="Analyserer...")
            n = len(st.session_state.watchlist)
            for i, t in enumerate(st.session_state.watchlist):
                analyze_ticker(t)  # populates cache
                progress.progress((i + 1) / n, text=f"Analyserer {t}...")
            progress.empty()

        for t in list(st.session_state.watchlist):
            df, err = fetch_ticker_df(t, period="6mo")
            with st.container():
                st.markdown('<div class="stock-card">', unsafe_allow_html=True)
                top = st.columns([4, 1])
                if df is not None and len(df) >= 2:
                    last, prev = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
                    chg_pct = (last / prev - 1.0) * 100.0
                    chg_class = "change-pos" if chg_pct >= 0 else "change-neg"
                    top[0].markdown(
                        f'<div class="stock-top"><b>{t}</b>&nbsp;'
                        f'<span class="stock-price">{last:,.2f}</span>&nbsp;'
                        f'<span class="{chg_class}">{chg_pct:+.2f}%</span></div>',
                        unsafe_allow_html=True,
                    )
                    st.line_chart(df["Close"].tail(90), height=100)
                else:
                    top[0].markdown(f"**{t}** — <span class='stock-name'>kunne ikke hente kurs ({err})</span>",
                                     unsafe_allow_html=True)
                if top[1].button("Fjern", key=f"remove_{t}"):
                    st.session_state.watchlist.remove(t)
                    save_watchlist(st.session_state.watchlist)
                    st.rerun()

                # analyze_ticker er @st.cache_data(ttl=900) — dette genberegner ikke,
                # det henter blot det seneste cachede resultat (eller beregner første gang).
                try:
                    result = analyze_ticker(t)
                except Exception:
                    result = {"ok": False, "reason": "analyse fejlede"}

                if result.get("ok"):
                    score = result["composite_score"]
                    cls = "rating-high" if score >= 65 else ("rating-mid" if score >= 45 else "rating-low")
                    upside_txt = f"{result['upside_pct']:+.1f}%" if result["upside_pct"] is not None else "–"
                    st.markdown(
                        f'<span class="rating-badge {cls}">Rating: {score:.0f}/100</span>&nbsp;'
                        f'<span class="stock-name">Upside: {upside_txt} ({result["upside_kilde"]})</span>',
                        unsafe_allow_html=True,
                    )
                    with st.expander("Detaljer"):
                        st.write("Tekniske triggere:", result["ta"]["ta_triggers"] or "Ingen")
                        a = result["analyst"]
                        if a.get("ok") and a.get("recommendation_key"):
                            st.write(f"Analytiker-anbefaling: {a['recommendation_key']} "
                                     f"({a.get('num_analysts', '?')} analytikere)")
                        st.caption("Ikke finansiel rådgivning — se README for forbehold.")
                st.markdown("</div>", unsafe_allow_html=True)

# --- AI Chat-fane ---
with tab_chat:
    st.caption(
        "AI-chat er endnu ikke koblet til en rigtig AI-model — det er bevidst fravalgt i "
        "første version (kræver en betalt API-nøgle). Skelettet er her, så vi nemt kan "
        "koble en rigtig model på senere, hvis du vil det."
    )
    for msg in st.session_state.chat_history:
        with st.chat_message(msg["role"]):
            st.write(msg["content"])
    if prompt := st.chat_input("Skriv en besked..."):
        st.session_state.chat_history.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.write(prompt)
        reply = "AI-chat er ikke aktiveret endnu. Sig til i vores samtale, så sætter vi en rigtig AI-nøgle op."
        st.session_state.chat_history.append({"role": "assistant", "content": reply})
        with st.chat_message("assistant"):
            st.write(reply)

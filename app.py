"""
Mine Aktier — mobil-app-lignende GUI til watchlist + rating.

Al analyselogik (univers, teknisk/fundamental/sentiment-analyse, komposit-
score, KØB/HOLD/SÆLG-anbefaling, handelsplan) bor i `core.py`, som denne
fil importerer fra og lægger Streamlit-caching ovenpå. `core.py` er delt
med `alert_check.py` (det automatiske daglige tjek, se README.md), som
IKKE kører i Streamlit og derfor ikke kan bruge st.cache_data.

Funktioner:
- Watchlist: tilføj/fjern aktier (skriv frit, fx "novo" — appen slår selv
  den rigtige Yahoo-ticker op).
- Hvert kort viser: navn, seneste kurs, dagens ændring, en mini-graf
  (som "Værdipapirer"-appen), børs/land/valuta, og — efter analyse — en
  komposit-rating (0-100), et upside-estimat og en KØB/HOLD/SÆLG-
  anbefaling. Komposit-scoren vægter teknisk analyse (kort+lang bane,
  30%), fundamentale nøgletal (25%) og popularitet/analytiker-tiltro
  (45%, vægtet tungest) sammen.
- AI chat-fane: UI-skelettet er der, men svarene er statiske indtil en
  rigtig API-nøgle kobles på (bevidst fravalgt i første version — se
  README.md).
- Top 10-fane: scanner hele det kuraterede aktieunivers (~93 aktier på
  tværs af USA/Europa/Norden/emerging markets) og viser de 10 bedst
  rangerede kandidater, inkl. et konkret handelsforslag (indgangskurs,
  stop-loss-niveau, indgangsvindue og en tidsbaseret stopud-dato).
- Automatisk dagligt tjek (uden for selve appen — se `alert_check.py` og
  README.md): tjekker watchlisten og Top 10 og sender en gratis push-
  notifikation, hvis noget viser et KØB- eller SÆLG-signal.

Kør lokalt: streamlit run app.py
Deploy: se README.md, afsnittet "Mine Aktier (mobil-app)".
"""

import os
import json
import html as _htmllib

import streamlit as st

from core import (
    Config,
    fetch_ticker_df, resolve_ticker,
    get_ticker_meta as _get_ticker_meta_impl,
    analyze_ticker as _analyze_ticker_impl,
    find_top_candidates as _find_top_candidates_impl,
)


def _esc(s):
    return _htmllib.escape(str(s)) if s is not None else ""


# ============================================================================
# --- Caching (kernelogikken bor i core.py, delt med alert_check.py) ---
# ============================================================================

@st.cache_data(ttl=3600, show_spinner=False)
def get_ticker_meta(ticker: str) -> dict:
    return _get_ticker_meta_impl(ticker)


@st.cache_data(ttl=900, show_spinner=False)
def analyze_ticker(ticker: str):
    """Cachet 15 min. så gentagne klik ikke spammer Yahoo Finance/StockTwits."""
    return _analyze_ticker_impl(ticker)


@st.cache_data(ttl=1800, show_spinner=False)
def find_top_candidates(top_n: int = 10):
    """Cachet 30 min."""
    return _find_top_candidates_impl(top_n)


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

tab_watch, tab_top10, tab_chat = st.tabs(["Watchlist", "Top 10", "AI Chat"])

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
        raw = new_ticker.strip()
        with st.spinner(f"Slår '{raw}' op..."):
            resolved_symbol, resolved_name, resolved_exchange = resolve_ticker(raw)
        t = resolved_symbol or raw.upper()
        if t not in st.session_state.watchlist:
            st.session_state.watchlist.append(t)
            save_watchlist(st.session_state.watchlist)
            if resolved_symbol:
                boers = f" — {resolved_exchange}" if resolved_exchange else ""
                st.session_state["_last_add_msg"] = f"Tilføjede {t} ({resolved_name}{boers})"
            else:
                st.session_state["_last_add_msg"] = (
                    f"Kunne ikke slå '{raw}' op automatisk — tilføjede den som skrevet ({t}). "
                    "Ret den med Fjern/Tilføj hvis det ikke er den rigtige ticker."
                )
        else:
            st.session_state["_last_add_msg"] = f"{t} er allerede på din watchlist."
        st.rerun()

    if st.session_state.get("_last_add_msg"):
        st.success(st.session_state.pop("_last_add_msg"))

    if not st.session_state.watchlist:
        st.info("Din watchlist er tom — tilføj en aktie ovenfor for at komme i gang.")
    else:
        st.caption(
            "Vil du have disse tjekket automatisk hver dag (med push-notifikation ved "
            "KØB/SÆLG-signal)? Læg tickerne i `watchlist_tickers.txt` på GitHub — se "
            "README.md, afsnittet \"Automatisk dagligt tjek\"."
        )
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

                meta = get_ticker_meta(t)
                meta_bits = [b for b in [meta.get("exchange"), meta.get("country"), meta.get("currency")] if b]
                if meta_bits:
                    top[0].markdown(f'<span class="stock-name">{" · ".join(meta_bits)}</span>',
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
                    rec = result.get("recommendation", {})
                    action = rec.get("action", "–")
                    action_cls = {"KØB": "rating-high", "HOLD": "rating-mid", "SÆLG": "rating-low"}.get(action, "rating-mid")
                    upside_txt = f"{result['upside_pct']:+.1f}%" if result["upside_pct"] is not None else "–"
                    st.markdown(
                        f'<span class="rating-badge {action_cls}">{_esc(action)}</span>&nbsp;'
                        f'<span class="rating-badge {cls}">Rating: {score:.0f}/100</span>&nbsp;'
                        f'<span class="stock-name">Upside: {upside_txt} ({result["upside_kilde"]})</span>',
                        unsafe_allow_html=True,
                    )
                    with st.expander("Detaljer"):
                        if rec.get("sell_triggers"):
                            st.write("Sælg-signaler:", rec["sell_triggers"])
                        st.write("Tekniske triggere (kort bane):", result["ta"]["ta_triggers"] or "Ingen")
                        lt = result.get("long_term", {})
                        if lt.get("ok"):
                            st.write("Langsigtede triggere (lang bane):", lt["long_term_triggers"] or "Ingen")
                        fa = result.get("fundamental", {})
                        if fa.get("ok") and fa.get("fundamental_triggers"):
                            st.write("Fundamentale styrker:", fa["fundamental_triggers"])
                        elif fa.get("ok") is False:
                            st.caption(f"Fundamentale nøgletal: ingen data ({fa.get('reason', '?')})")
                        a = result["analyst"]
                        if a.get("ok") and a.get("recommendation_key"):
                            st.write(f"Analytiker-anbefaling: {a['recommendation_key']} "
                                     f"({a.get('num_analysts', '?')} analytikere)")
                        plan = result.get("plan", {})
                        if plan.get("stop_price") is not None:
                            st.caption(f"Stop-niveau: {plan['stop_price']:.2f} ({plan.get('stop_kilde', '?')})")
                        st.caption("Ikke finansiel rådgivning — se README for forbehold.")
                st.markdown("</div>", unsafe_allow_html=True)

# --- Top 10-fane ---
with tab_top10:
    st.caption(
        "Scanner det kuraterede aktieunivers (~93 aktier på tværs af USA, Europa, "
        "Norden og emerging markets — samme liste som kandidat-screeneren) og "
        "rangerer efter teknisk (kort+lang bane, 30%), fundamental (25%) og "
        "popularitet/analytiker-tiltro (45%) — populæritet vægtes tungest. "
        "Ikke 'alle aktier på Yahoo Finance' — det er ikke teknisk muligt at scanne "
        "uden at blive rate-limitet. Tager typisk 2-4 minutter; resultatet caches i "
        "30 minutter, så du ikke behøver vente hver gang. Top 10 tjekkes desuden "
        "automatisk hver dag — se README.md, afsnittet \"Automatisk dagligt tjek\"."
    )
    st.warning(
        "Handelsforslagene (indgangskurs, stop-loss, datoer) og fundamental-scoren "
        "er regelbaserede beregninger til videre research — ikke en anbefaling "
        "eller garanti. Stop-loss-niveauet (tekniske niveau eller -10%-loft, alt "
        "efter hvad der er strammest) og fundamental-scoren er nye regler, der "
        "IKKE er backtestet endnu, i modsætning til resten af værktøjet. "
        "Fundamental-scoren sammenligner heller ikke inden for samme branche "
        "(en bank og en tech-aktie vurderes med samme skala)."
    )

    if st.button("🔍 Find top 10 kandidater", use_container_width=True):
        with st.spinner("Scanner universet — dette tager et par minutter..."):
            top_candidates, scan_stats = find_top_candidates(top_n=10)
        st.session_state["_top10_result"] = (top_candidates, scan_stats)

    cached = st.session_state.get("_top10_result")
    if not cached:
        st.info("Tryk på knappen ovenfor for at køre en scanning.")
    else:
        top_candidates, scan_stats = cached
        st.caption(
            f"Scannede {scan_stats['universe_size']} aktier · "
            f"{scan_stats['skipped_data']} kunne ikke hentes · "
            f"{scan_stats['skipped_trend']} bestod ikke trendfilteret · "
            f"{scan_stats['shortlist_size']} gik videre til analytiker-/sentiment-analyse."
        )
        if not top_candidates:
            st.info("Ingen kandidater bestod trendfilteret i denne scanning.")
        for rank, c in enumerate(top_candidates, 1):
            ta, plan = c["ta"], c["plan"]
            with st.container():
                st.markdown('<div class="stock-card">', unsafe_allow_html=True)
                score = c["composite_score"]
                cls = "rating-high" if score >= 65 else ("rating-mid" if score >= 45 else "rating-low")
                rec = c.get("recommendation", {})
                action = rec.get("action", "–")
                action_cls = {"KØB": "rating-high", "HOLD": "rating-mid", "SÆLG": "rating-low"}.get(action, "rating-mid")
                st.markdown(
                    f'<div class="stock-top"><b>#{rank} {c["ticker"]}</b>&nbsp;'
                    f'<span class="stock-name">{_esc(c["short_name"])} · {c["region"]}</span></div>'
                    f'<span class="rating-badge {action_cls}">{_esc(action)}</span>&nbsp;'
                    f'<span class="rating-badge {cls}">Score: {score:.0f}/100</span>',
                    unsafe_allow_html=True,
                )
                meta_bits = [b for b in [c["meta"].get("exchange"), c["meta"].get("country"),
                                          c["meta"].get("currency")] if b]
                if meta_bits:
                    st.markdown(f'<span class="stock-name">{" · ".join(meta_bits)}</span>',
                                unsafe_allow_html=True)

                upside_txt = f"{c['upside_pct']:+.1f}%" if c["upside_pct"] is not None else "–"
                st.write(f"Kurs: **{ta['last_price']:.2f}** · Upside: **{upside_txt}** ({c['upside_kilde']})")

                cols = st.columns(2)
                cols[0].metric("Indgangskurs (forslag)", f"{plan['entry_price']:.2f}")
                cols[0].caption(f"Gyldigt til: {plan['entry_deadline']} (ellers er signalet forældet)")
                if plan["stop_price"] is not None:
                    cols[1].metric("Stop-loss (forslag)", f"{plan['stop_price']:.2f}", f"{plan['stop_pct']:.1f}%")
                    cols[1].caption(f"Kilde: {plan['stop_kilde']}")
                else:
                    cols[1].metric("Stop-loss (forslag)", "–")
                    cols[1].caption("Intet klart niveau under nuværende kurs")
                st.caption(f"Stopud dato (maks. holdeperiode ~{Config.swing_hold_days} handelsdage): "
                           f"**{plan['stop_out_date']}** — luk positionen her hvis hverken mål eller stop er nået.")

                with st.expander("Triggere, nøgletal og kilder"):
                    if rec.get("sell_triggers"):
                        st.write("Sælg-signaler:", rec["sell_triggers"])
                    st.write("Tekniske triggere (kort bane):", ta["ta_triggers"] or "Ingen")
                    lt = c.get("long_term", {})
                    if lt.get("ok"):
                        st.write("Langsigtede triggere (lang bane):", lt["long_term_triggers"] or "Ingen")
                    fa = c.get("fundamental", {})
                    if fa.get("ok") and fa.get("fundamental_triggers"):
                        st.write("Fundamentale styrker:", fa["fundamental_triggers"])
                    elif fa.get("ok") is False:
                        st.caption(f"Fundamentale nøgletal: ingen data ({fa.get('reason', '?')})")
                    a = c["analyst"]
                    if a.get("ok") and a.get("recommendation_key"):
                        st.write(f"Analytiker-anbefaling: {a['recommendation_key']} "
                                 f"({a.get('num_analysts', '?')} analytikere)")
                    st.caption("Ikke finansiel rådgivning — se README for forbehold.")

                if c["ticker"] not in st.session_state.watchlist:
                    if st.button("➕ Tilføj til watchlist", key=f"add_top10_{c['ticker']}"):
                        st.session_state.watchlist.append(c["ticker"])
                        save_watchlist(st.session_state.watchlist)
                        st.rerun()
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

"""
Kandidat-screener: rangerer aktier efter "hvor moden er den til køb lige nu",
kombineret af teknisk analyse (TA), analytiker-konsensus og popularitet/
sentiment (analytikere + StockTwits + Reddit).

Dette er IKKE det samme som backtest-strategien i strategy.py/backtest.py.
Screeneren er et forward-looking øjebliksbillede ("hvad ser interessant ud
lige nu") og bruger data der ikke findes historisk på en måde der kan
no-lookahead-backtestes (analytikerkonsensus og social sentiment findes
kun for "i dag", ikke pænt tidsstemplet bagud i tid). Den erstatter ikke
backtesten — brug den som et supplerende research-værktøj.

**Vigtigt: dette er IKKE finansiel rådgivning.** Scoren er en gennemsigtig,
regelbaseret sammenvejning af offentligt tilgængelige signaler — ikke en
forudsigelse. Lav altid egen research før du handler, og husk at analytiker-
kursmål og social sentiment jævnligt tager fejl.

Fremgangsmåde (holder antal eksterne API-kald nede):
1. Hent kursdata for hele universet (samme cache som backtesten).
2. Beregn TA-signaler for alle tickere (gratis — ingen ekstra netværkskald,
   bruger allerede hentede kursdata) og byg en kort shortlist af de bedst
   rangerede TA-kandidater.
3. Kun for shortlisten: hent analytiker-data (yfinance), StockTwits og
   Reddit (best-effort, kan mangle for nogle/alle tickere).
4. Kombinér til en komposit-score og rangér.
"""

import sys
import time

import pandas as pd

from config import Config
from universe import full_universe, region_of, BENCHMARK
from data_fetch import fetch_all
from technicals import compute_technical_signals
from sentiment import get_analyst_data, get_stocktwits_data, get_reddit_data


def _compute_upside(ta: dict, analyst: dict):
    """Returnér (upside_pct eller None, kilde-tekst)."""
    last_price = ta["last_price"]
    if analyst.get("ok") and analyst.get("target_mean") and last_price:
        upside = (analyst["target_mean"] / last_price - 1.0) * 100.0
        n = analyst.get("num_analysts")
        kilde = f"Analytiker-konsensus ({n} analytikere)" if n else "Analytiker-konsensus"
        return upside, kilde
    high_252 = ta.get("high_252w")
    if high_252 and last_price and last_price < high_252:
        upside = (high_252 / last_price - 1.0) * 100.0
        return upside, "Teknisk estimat (afstand til 52-ugers højeste) — intet analytikerdækning"
    return None, "Intet analytikerdækning og ingen klar teknisk målkurs (aktien er allerede ved/over 52-ugers højeste)"


def _popularity_score(analyst: dict, stocktwits: dict, reddit_buzz_pct):
    components, weights = [], []
    if analyst.get("ok") and analyst.get("analyst_score") is not None:
        components.append(analyst["analyst_score"]); weights.append(0.5)
    if stocktwits.get("ok") and stocktwits.get("stocktwits_score") is not None:
        components.append(stocktwits["stocktwits_score"]); weights.append(0.3)
    if reddit_buzz_pct is not None:
        components.append(reddit_buzz_pct); weights.append(0.2)
    if not components:
        return None
    total_w = sum(weights)
    return sum(c * w for c, w in zip(components, weights)) / total_w


def run_screener(cfg: Config, progress=print) -> list:
    """Kør hele screener-pipelinen. Returnerer en liste af dicts, én pr.
    kandidat, sorteret efter komposit-score (højest først)."""

    progress("Henter kursdata (samme cache som backtesten)...")
    data = fetch_all(cfg)
    data.pop(BENCHMARK, None)

    progress(f"\nBeregner tekniske signaler for {len(data)} tickere...")
    ta_results = {}
    for ticker, df in data.items():
        res = compute_technical_signals(df, cfg)
        if res.get("ok"):
            ta_results[ticker] = res

    trend_ok_count = sum(1 for r in ta_results.values() if r["trend_ok"])
    progress(f"{len(ta_results)} tickere havde nok historik, {trend_ok_count} bestod trendfilteret.")

    shortlist = sorted(
        (t for t, r in ta_results.items() if r["trend_ok"]),
        key=lambda t: ta_results[t]["ta_score"],
        reverse=True,
    )[: cfg.screener_shortlist_size]

    if not shortlist:
        progress("Ingen kandidater bestod trendfilteret — ingen aktier at rangere lige nu.")
        return []

    progress(f"\nShortlist ({len(shortlist)} tickere) — henter analytiker-/sentiment-data pr. aktie...")
    candidates = []
    reddit_mentions = {}
    for i, ticker in enumerate(shortlist, 1):
        progress(f"[{i}/{len(shortlist)}] {ticker}")
        ta = ta_results[ticker]

        analyst = get_analyst_data(ticker)
        stocktwits = get_stocktwits_data(ticker, cfg)
        reddit = get_reddit_data(ticker, cfg, company_name=analyst.get("short_name"))
        if reddit.get("ok"):
            reddit_mentions[ticker] = reddit["mention_count"]

        candidates.append({
            "ticker": ticker, "region": region_of(ticker),
            "ta": ta, "analyst": analyst, "stocktwits": stocktwits, "reddit": reddit,
        })
        time.sleep(cfg.sentiment_request_delay_sec)

    # Reddit-buzz er kun meningsfuld relativt til de andre kandidater i denne
    # kørsel (rå antal opslag siger ikke meget alene) — percentilrangér.
    if reddit_mentions:
        s = pd.Series(reddit_mentions)
        reddit_pct = (s.rank(pct=True) * 100.0).to_dict()
    else:
        reddit_pct = {}

    for c in candidates:
        ticker = c["ticker"]
        pop_score = _popularity_score(c["analyst"], c["stocktwits"], reddit_pct.get(ticker))
        c["popularity_score"] = pop_score
        ta_score = c["ta"]["ta_score"]
        sent_score = pop_score if pop_score is not None else 50.0  # neutral hvis intet data
        c["composite_score"] = (
            cfg.screener_ta_weight * ta_score + cfg.screener_sentiment_weight * sent_score
        )
        upside_pct, upside_kilde = _compute_upside(c["ta"], c["analyst"])
        c["upside_pct"] = upside_pct
        c["upside_kilde"] = upside_kilde
        c["reddit_buzz_percentile"] = reddit_pct.get(ticker)

    candidates.sort(key=lambda c: c["composite_score"], reverse=True)
    return candidates[: cfg.screener_top_n]


if __name__ == "__main__":
    cfg = Config()
    results = run_screener(cfg)
    print("\n=== Topkandidater ===")
    for c in results:
        print(f"{c['ticker']:12s} score={c['composite_score']:5.1f}  "
              f"TA={c['ta']['ta_score']:5.1f}  pop={c['popularity_score']}")

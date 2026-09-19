"""
Analytiker- og sentiment-data til kandidat-screeneren.

Tre kilder, hver med faldende pålidelighed — alle er "best effort" og fejler
ALDRIG hårdt: returnerer altid et dict med "ok": True/False + en forklaring,
så én kildes nedetid ikke vælter hele screener-kørslen.

1. Analytiker-data (yfinance/Yahoo Finance): kursmål, antal analytikere,
   købs-/salgsanbefaling, seneste op-/nedgraderinger. Relativt pålideligt,
   ingen ekstra afhængighed (bruger allerede-installeret yfinance).

2. StockTwits (offentligt, uautoriseret API — api.stocktwits.com): "bullish/
   bearish"-tags på seneste opslag + watchlist-antal som popularitetsmål.
   Dækker stort set kun US-tickere/ADR'er (ikke europæiske/nordiske
   hjemmemarkeds-tickere). Ingen garanti for oppetid/rate-limits — det er en
   uofficiel, uautentificeret endpoint, IKKE testet fra dette sandbox-miljø
   (ingen internetadgang her), så behandl fejl herfra som forventelige.

3. Reddit (praw): tæller opslag der nævner tickeren/selskabet i udvalgte
   subreddits seneste uge. KRÆVER at du selv opretter en gratis Reddit "app"
   (script-type) på reddit.com/prefs/apps og gemmer client_id/secret som
   Colab-secrets (nøgleikonet i venstre sidebjælke) under navnene
   REDDIT_CLIENT_ID og REDDIT_CLIENT_SECRET — se README.md. Uden dette
   udelades Reddit-delen automatisk (ingen fejl, bare et "ikke konfigureret").
"""

import os
import time

import pandas as pd


# ---------------------------------------------------------------------
# 1) Analytiker-data (yfinance)
# ---------------------------------------------------------------------

def get_analyst_data(ticker: str) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        return {"ok": False, "reason": "yfinance ikke installeret"}

    try:
        t = yf.Ticker(ticker)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass

        target_mean = info.get("targetMeanPrice")
        target_high = info.get("targetHighPrice")
        target_low = info.get("targetLowPrice")
        num_analysts = info.get("numberOfAnalystOpinions")
        rec_mean = info.get("recommendationMean")  # 1=Strong Buy .. 5=Sell
        rec_key = info.get("recommendationKey")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        short_name = info.get("shortName") or info.get("longName")

        upgrades_90d, downgrades_90d = None, None
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                idx = ud.index if not isinstance(ud.index, pd.RangeIndex) else pd.to_datetime(ud.get("GradeDate", ud.index))
                ud = ud.copy()
                ud.index = pd.to_datetime(idx, errors="coerce", utc=True).tz_localize(None) if hasattr(idx, "tz") else pd.to_datetime(idx, errors="coerce")
                cutoff = pd.Timestamp.today() - pd.Timedelta(days=90)
                recent = ud[ud.index >= cutoff]
                action_col = "Action" if "Action" in recent.columns else None
                if action_col:
                    upgrades_90d = int((recent[action_col].str.lower() == "up").sum())
                    downgrades_90d = int((recent[action_col].str.lower() == "down").sum())
        except Exception:
            pass  # upgrades/downgrades er "nice to have" — fejl her er ikke fatalt

        analyst_score = None
        if rec_mean is not None:
            score_rec = max(0.0, min(100.0, (5.0 - float(rec_mean)) / 4.0 * 100.0))
            if upgrades_90d is not None and downgrades_90d is not None:
                momentum = upgrades_90d - downgrades_90d
                momentum_score = max(0.0, min(100.0, 50.0 + momentum * 10.0))
                analyst_score = 0.7 * score_rec + 0.3 * momentum_score
            else:
                analyst_score = score_rec

        return {
            "ok": True,
            "current_price": current_price,
            "short_name": short_name,
            "target_mean": target_mean,
            "target_high": target_high,
            "target_low": target_low,
            "num_analysts": num_analysts,
            "recommendation_mean": rec_mean,
            "recommendation_key": rec_key,
            "upgrades_90d": upgrades_90d,
            "downgrades_90d": downgrades_90d,
            "analyst_score": analyst_score,
        }
    except Exception as e:
        return {"ok": False, "reason": f"kunne ikke hente analytiker-data: {e}"}


# ---------------------------------------------------------------------
# 2) StockTwits
# ---------------------------------------------------------------------

def get_stocktwits_data(ticker: str, cfg) -> dict:
    try:
        import requests
    except ImportError:
        return {"ok": False, "reason": "requests ikke installeret"}

    symbol = ticker.split(".")[0]  # StockTwits kender kun US-stil symboler
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (swing_agent screener; personligt analyseværktøj)"},
            timeout=8,
        )
        if resp.status_code != 200:
            return {"ok": False, "reason": f"StockTwits svarede HTTP {resp.status_code} (rate-limit/ukendt symbol?)"}
        data = resp.json()
        watchlist_count = (data.get("symbol") or {}).get("watchlist_count")
        messages = data.get("messages") or []
        tagged = [
            m for m in messages
            if (m.get("entities") or {}).get("sentiment") is not None
        ]
        bullish = sum(
            1 for m in tagged
            if (m["entities"]["sentiment"] or {}).get("basic") == "Bullish"
        )
        sample_size = len(tagged)
        bullish_ratio = (bullish / sample_size * 100.0) if sample_size else None

        stocktwits_score = None
        if bullish_ratio is not None:
            weight = min(sample_size / 10.0, 1.0)
            stocktwits_score = weight * bullish_ratio + (1 - weight) * 50.0

        return {
            "ok": True,
            "watchlist_count": watchlist_count,
            "message_count": len(messages),
            "tagged_sample_size": sample_size,
            "bullish_ratio": bullish_ratio,
            "stocktwits_score": stocktwits_score,
        }
    except Exception as e:
        return {"ok": False, "reason": f"StockTwits-opslag fejlede: {e}"}


# ---------------------------------------------------------------------
# 3) Reddit (praw) — kræver Colab-secrets, udelades gracefully uden
# ---------------------------------------------------------------------

def _get_secret(name: str):
    try:
        from google.colab import userdata  # kun tilgængelig i Colab
        val = userdata.get(name)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(name)


def get_reddit_data(ticker: str, cfg, company_name: str = None) -> dict:
    try:
        import praw
    except ImportError:
        return {"ok": False, "reason": "praw ikke installeret — Reddit-sentiment udelades (pip install praw)"}

    client_id = _get_secret("REDDIT_CLIENT_ID")
    client_secret = _get_secret("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return {
            "ok": False,
            "reason": "Reddit API-nøgler ikke fundet (Colab-secrets REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET) — se README.md",
        }

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="swing_agent_screener/1.0 (personligt analyseværktøj)",
        )
        reddit.read_only = True

        base_symbol = ticker.split(".")[0]
        search_terms = {base_symbol}
        if company_name:
            search_terms.add(company_name)

        mentions = []
        for sub_name in cfg.reddit_subreddits:
            try:
                subreddit = reddit.subreddit(sub_name)
                for term in search_terms:
                    for post in subreddit.search(term, time_filter=cfg.reddit_time_filter,
                                                  limit=cfg.reddit_search_limit):
                        mentions.append({
                            "subreddit": sub_name,
                            "title": post.title,
                            "score": post.score,
                            "num_comments": post.num_comments,
                        })
            except Exception:
                continue  # én subreddit der fejler (fx nedlagt/privat) må ikke vælte resten

        # dedupliker (samme post kan matche flere søgetermer)
        seen = set()
        unique = []
        for m in mentions:
            key = (m["subreddit"], m["title"])
            if key not in seen:
                seen.add(key)
                unique.append(m)

        avg_score = (sum(m["score"] for m in unique) / len(unique)) if unique else 0.0
        return {
            "ok": True,
            "mention_count": len(unique),
            "avg_score": avg_score,
            "top_mentions": sorted(unique, key=lambda m: m["score"], reverse=True)[:3],
        }
    except Exception as e:
        return {"ok": False, "reason": f"Reddit-opslag fejlede: {e}"}

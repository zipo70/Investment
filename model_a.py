"""
model_a.py — "Model A": den eneste model der bestod den realistiske
validering (validate_models.py, 2026-10-25).

Regler (identiske med backtest.py / backtest_v3 model A):
  1. Univers: det kuraterede univers (universe.full_universe), minus ACWI.
  2. Trendfilter: kurs over 200-dages glidende gennemsnit.
  3. Rangering: 6-1 måneders momentum = afkast fra 126 til 21 handelsdage
     siden (seneste måned springes over).
  4. Porteføljen = top 5, ligevægtet (20 % hver).
  5. Rebalancering hver 63. handelsdag (~kvartalsvis) regnet fra ANCHOR.
     Kun på rebalanceringsdage købes/sælges der efter rangeringen.
  6. Stop-loss −15 % fra indgangskurs gælder altid (lagt i ordren hos Saxo).

Kompositscore, popularitet, Aktieguld, GEICO og kursmønstre bruges IKKE til
beslutninger længere — de vises kun som information. Valideringen viste at
de forringede eller ikke kunne efterprøves.

Research-værktøj, ikke finansiel rådgivning.
"""

from __future__ import annotations

import datetime as dt
import time

import numpy as np

N_POSITIONS = 5
LOOKBACK_DAYS = 126
SKIP_DAYS = 21
SMA_WINDOW = 200
STOP_PCT = 0.15
REBALANCE_EVERY_DAYS = 63
ANCHOR = dt.date(2026, 10, 26)  # første rebalanceringsdag (mandag)
CACHE_TTL_SEC = 3600

_cache = {"ts": 0.0, "data": None}


def signals_for_df(df) -> dict:
    """Trend + 6-1 momentum for én aktie (bruger justeret kurs hvis den findes)."""
    col = "AdjClose" if "AdjClose" in df.columns else "Close"
    c = df[col].dropna().to_numpy(float)
    if len(c) < max(SMA_WINDOW, LOOKBACK_DAYS) + 2:
        return {"ok": False, "reason": "for kort historik"}
    sma = float(c[-SMA_WINDOW:].mean())
    mom = float(c[-1 - SKIP_DAYS] / c[-1 - LOOKBACK_DAYS] - 1)
    return {"ok": True, "trend_ok": bool(c[-1] > sma), "momentum": mom,
            "last": float(c[-1]), "sma200": sma}


def rebalance_info(today: dt.date | None = None) -> dict:
    today = today or dt.date.today()
    if today < ANCHOR:
        return {"is_rebalance_day": False, "next": ANCHOR, "last": None}
    n = int(np.busday_count(ANCHOR, today))
    k = n // REBALANCE_EVERY_DAYS
    last = np.busday_offset(np.datetime64(ANCHOR), k * REBALANCE_EVERY_DAYS, roll="forward")
    nxt = np.busday_offset(np.datetime64(ANCHOR), (k + 1) * REBALANCE_EVERY_DAYS, roll="forward")
    return {"is_rebalance_day": bool(np.busday_count(ANCHOR, today) % REBALANCE_EVERY_DAYS == 0
                                     and np.is_busday(np.datetime64(today))),
            "last": last.astype(object), "next": nxt.astype(object)}


def ranking(force: bool = False, delay_sec: float = 0.3) -> dict:
    """Scanner universet og returnerer model A's rangering. Caches i 1 time
    i processen (Streamlit, GitHub-jobs), da hver kørsel henter ~80 tickere."""
    if not force and _cache["data"] is not None and time.time() - _cache["ts"] < CACHE_TTL_SEC:
        return _cache["data"]
    from core import fetch_ticker_df, full_universe, region_of, BENCHMARK

    rows, skipped_data = [], 0
    universe = [t for t in full_universe() if t != BENCHMARK]
    for i, t in enumerate(universe):
        df, _ = fetch_ticker_df(t, period="2y")
        s = signals_for_df(df) if df is not None else {"ok": False}
        if not s.get("ok"):
            skipped_data += 1
        else:
            rows.append({"ticker": t, "region": region_of(t), **s})
        if i < len(universe) - 1:
            time.sleep(delay_sec)

    eligible = sorted([r for r in rows if r["trend_ok"]], key=lambda r: r["momentum"], reverse=True)
    for k, r in enumerate(eligible, 1):
        r["rank"] = k
    by_ticker = {r["ticker"]: r for r in rows}
    data = {
        "as_of": dt.date.today().isoformat(),
        "ranked": eligible,
        "top": [r["ticker"] for r in eligible[:N_POSITIONS]],
        "by_ticker": by_ticker,
        "universe_size": len(universe),
        "skipped_data": skipped_data,
        "skipped_trend": len(rows) - len(eligible),
        "rebalance": rebalance_info(),
    }
    _cache.update(ts=time.time(), data=data)
    return data


def action_for(ticker: str, sig: dict, rank_data: dict) -> dict:
    """Model A's vurdering af én aktie, til visning i app og notifikationer."""
    reb = rank_data["rebalance"]
    nxt = reb["next"]
    r = rank_data["by_ticker"].get(ticker)
    rank = r.get("rank") if r else None
    mom_txt = f"6-1 mdr. momentum {sig['momentum'] * 100:+.1f}%" if sig.get("ok") else "momentum ukendt"
    if ticker in rank_data["top"]:
        action = "KØB"
        why = [f"I model A's top {N_POSITIONS} (#{rank}) — {mom_txt}",
               "Købes på rebalanceringsdagen" + ("" if reb["is_rebalance_day"] else f" (næste: {nxt})")]
    elif sig.get("ok") and not sig.get("trend_ok"):
        action = "SÆLG"
        why = [f"Under 200-dages snit ({sig['last']:.2f} < {sig['sma200']:.2f}) — ikke købbar",
               f"Ejer du den, sælges den ved næste rebalancering ({nxt}) medmindre den er tilbage i top {N_POSITIONS}"]
    else:
        action = "HOLD"
        why = [f"Uden for top {N_POSITIONS}" + (f" (#{rank})" if rank else "") + f" — {mom_txt}",
               f"Ejer du den, beholdes den til næste rebalancering ({nxt}); "
               f"stop −{STOP_PCT * 100:.0f}% gælder altid"]
    return {"action": action, "model": "A", "rank": rank, "reasons": why,
            "sell_triggers": why if action == "SÆLG" else [], "sell_score": 0}

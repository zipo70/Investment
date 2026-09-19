"""
Trend/momentum swing-signal.

Alle beregninger bruger UDELUKKENDE data til og med `as_of_date` (ingen
look-ahead). Det er en simpel, gennemsigtig regelbaseret model — ikke en
sort boks:

1. Trendfilter: aktien skal handle over sit 200-dages glidende gennemsnit.
   Fjerner aktier i langsigtet nedtrend fra kandidatlisten.
2. Momentum-score: afkast fra (t - lookback) til (t - skip), altså et
   "skip-month"-momentum (fx 6 mdr. afkast, seneste måned udeladt) — en
   standardkonstruktion i akademisk momentumforskning (Jegadeesh & Titman),
   der undgår kortsigtet mean-reversion-støj lige før rebalanceringsdatoen.

Kandidater rangeres efter momentum-score; kun aktier der består trendfilteret
er i det hele taget kandidater.
"""

import pandas as pd

from config import Config


def get_signals_as_of(prices_wide: pd.DataFrame, as_of_date, cfg: Config) -> pd.DataFrame:
    """Compute trend + momentum signals for every ticker, using only data up to
    and including as_of_date. Returns a DataFrame indexed by ticker with columns
    [last_price, sma, trend_ok, momentum]. Tickers without enough history are
    dropped (NaN)."""
    hist = prices_wide.loc[:as_of_date]
    n = len(hist)
    needed = max(cfg.trend_sma_window, cfg.momentum_lookback_days) + 1
    if n < needed:
        return pd.DataFrame(columns=["last_price", "sma", "trend_ok", "momentum"])

    sma = hist.rolling(cfg.trend_sma_window, min_periods=cfg.trend_sma_window).mean().iloc[-1]
    last = hist.iloc[-1]
    trend_ok = last > sma

    skip_idx = -1 - cfg.momentum_skip_days
    look_idx = -1 - cfg.momentum_lookback_days
    if abs(look_idx) > n or abs(skip_idx) > n:
        return pd.DataFrame(columns=["last_price", "sma", "trend_ok", "momentum"])

    p_skip = hist.iloc[skip_idx]
    p_look = hist.iloc[look_idx]
    momentum = (p_skip / p_look) - 1.0

    out = pd.DataFrame({
        "last_price": last,
        "sma": sma,
        "trend_ok": trend_ok,
        "momentum": momentum,
    })
    return out


def rank_candidates(prices_wide: pd.DataFrame, as_of_date, cfg: Config,
                     exclude=None) -> pd.DataFrame:
    """Return candidates passing the trend filter, sorted by momentum desc.
    `exclude` is a set of tickers to never consider (e.g. the benchmark)."""
    exclude = exclude or set()
    sig = get_signals_as_of(prices_wide, as_of_date, cfg)
    if sig.empty:
        return sig
    sig = sig.dropna()
    sig = sig[sig["trend_ok"]]
    sig = sig[~sig.index.isin(exclude)]
    sig = sig.sort_values("momentum", ascending=False)
    return sig


def target_weights(candidates: pd.DataFrame, prices_wide: pd.DataFrame,
                    as_of_date, cfg: Config) -> pd.Series:
    """Pick the top max_positions candidates and assign portfolio weights.
    Returns a Series {ticker: weight}, weights summing to <= 1.0 (remainder
    is cash if fewer than max_positions candidates qualify)."""
    top = candidates.head(cfg.max_positions)
    if top.empty:
        return pd.Series(dtype=float)

    if cfg.weighting == "equal":
        w = pd.Series(1.0 / cfg.max_positions, index=top.index)
    elif cfg.weighting == "inverse_vol":
        hist = prices_wide.loc[:as_of_date, top.index].tail(63)
        daily_ret = hist.pct_change().dropna()
        vol = daily_ret.std()
        vol = vol.replace(0, vol.mean())
        inv_vol = 1.0 / vol
        raw = inv_vol / inv_vol.sum()
        # scale so the whole basket still uses at most max_positions "slots"
        # worth of capital (comparable exposure to equal-weight)
        w = raw * (min(len(top), cfg.max_positions) / cfg.max_positions)
    else:
        raise ValueError(f"Ukendt weighting-metode: {cfg.weighting}")

    return w

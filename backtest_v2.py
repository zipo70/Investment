"""
backtest_v2.py — forbedringsforsøg på den originale swing-strategi.

Samme motor som backtest.py (dag-for-dag, handel til lukkekurs, ugentligt
−15 %-stop, 0,10 % kurtage / min. 29), men med valgfrie, teoribaserede
ændringer der kan slås til én ad gangen (V2Options). Med standardværdierne
opfører den sig som originalen. backtest.py er IKKE ændret.

Ændringer (alle med dokumentation i forskningen):
  momentum_lookback_days=252   12-1 måneders momentum (Jegadeesh & Titman m.fl.)
  market_filter=True           Kun investeret når ACWI > 200-dages snit,
                               tjekket hver `market_check_every_days`
                               (Faber 2007; Antonacci "dual momentum")
  vol_target=0.20              Skaler eksponering ned når kurvens volatilitet
                               er høj (Barroso & Santa-Clara 2015)
  max_positions=10/15          Mindre koncentration
  buffer_mult=2.0              Behold en aktie så længe den er i top 2×N
                               (færre handler, lavere omkostninger)
  smooth_momentum=True         "Frog in the pan": blandt top 2×N vælges de
                               N med mest jævn kursstigning (Da, Gurun &
                               Warachka 2014) — prisbaseret kvalitetsproxy,
                               da historiske fundamentale data ikke findes.

Research-værktøj, ikke finansiel rådgivning.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from universe import BENCHMARK

WARMUP = 260  # fælles opstart for alle varianter (nok til 12-1 momentum + SMA200)


@dataclass(frozen=True)
class V2Options:
    starting_capital: float = 100_000.0
    max_positions: int = 5
    rebalance_every_days: int = 63
    trend_sma_window: int = 200
    momentum_lookback_days: int = 126
    momentum_skip_days: int = 21
    stop_loss_pct: float = 0.15
    risk_check_every_days: int = 5
    cost_pct: float = 0.0010
    min_fee: float = 29.0
    market_filter: bool = False
    market_check_every_days: int = 21
    vol_target: float | None = None
    vol_lookback_days: int = 126
    buffer_mult: float = 1.0
    smooth_momentum: bool = False


def _fee(notional, o):
    return max(abs(notional) * o.cost_pct, o.min_fee)


def _ranked(prices: pd.DataFrame, i: int, o: V2Options, cols) -> pd.DataFrame:
    hist = prices.iloc[: i + 1][cols]
    last = hist.iloc[-1]
    sma = hist.iloc[-o.trend_sma_window:].mean()
    p_skip = hist.iloc[-1 - o.momentum_skip_days]
    p_look = hist.iloc[-1 - o.momentum_lookback_days]
    df = pd.DataFrame({"last": last, "sma": sma, "mom": p_skip / p_look - 1}).dropna()
    df = df[df["last"] > df["sma"]].sort_values("mom", ascending=False)
    if o.smooth_momentum and not df.empty:
        win = hist.iloc[-1 - o.momentum_lookback_days:-o.momentum_skip_days]
        r = win.pct_change().iloc[1:]
        pos, neg = (r > 0).mean(), (r < 0).mean()
        df["id"] = (np.sign(df["mom"]) * (neg - pos)).reindex(df.index)
    return df


def _select(df: pd.DataFrame, held: set, o: V2Options) -> list:
    n = o.max_positions
    if df.empty:
        return []
    if o.smooth_momentum:
        pool = df.head(2 * n)
        ordered = list(pool.sort_values("id").index)
    else:
        ordered = list(df.index)
    keep_zone = set(list(df.index)[: int(round(n * o.buffer_mult))])
    keep = [t for t in ordered if t in held and t in keep_zone][:n] if o.buffer_mult > 1 else []
    out = list(keep)
    for t in ordered:
        if len(out) >= n:
            break
        if t not in out:
            out.append(t)
    return out


def run_v2(prices: pd.DataFrame, o: V2Options) -> dict:
    dates = prices.index
    cols = [c for c in prices.columns if c != BENCHMARK]
    if len(dates) <= WARMUP + 5:
        raise RuntimeError("for lidt historik")
    px = prices.to_numpy(float)
    col_ix = {c: k for k, c in enumerate(prices.columns)}
    bench = prices[BENCHMARK]
    bench_sma = bench.rolling(200, min_periods=200).mean()

    cash = o.starting_capital
    pos: dict[str, dict] = {}
    equity, trades = {}, []
    last_reb = last_risk = last_mkt = None
    market_on = True

    def price(t, i):
        return px[i, col_ix[t]]

    def value(i):
        v = cash
        for t, p in pos.items():
            pr = price(t, i)
            if not np.isnan(pr):
                v += p["shares"] * pr
        return v

    def close(t, i, reason):
        nonlocal cash
        pr = price(t, i)
        if np.isnan(pr):
            return
        p = pos.pop(t)
        n = p["shares"] * pr
        cash += n - _fee(n, o)
        trades.append({"ticker": t, "entry": p["entry"], "exit": pr, "ret": pr / p["entry"] - 1,
                       "days": (dates[i] - p["date"]).days, "reason": reason})

    def open_(t, i, notional):
        nonlocal cash
        pr = price(t, i)
        if np.isnan(pr) or pr <= 0 or notional <= 0:
            return
        notional = min(notional, cash / (1 + o.cost_pct)) - o.min_fee
        if notional <= o.min_fee * 5:
            return
        cash -= notional + _fee(notional, o)
        pos[t] = {"shares": notional / pr, "entry": pr, "date": dates[i]}

    for i in range(WARMUP, len(dates)):
        # stop-loss
        if last_risk is None or i - last_risk >= o.risk_check_every_days:
            last_risk = i
            for t in list(pos):
                pr = price(t, i)
                if not np.isnan(pr) and pr / pos[t]["entry"] - 1 <= -o.stop_loss_pct:
                    close(t, i, "stop_loss")

        force_reb = False
        if o.market_filter and (last_mkt is None or i - last_mkt >= o.market_check_every_days):
            last_mkt = i
            b, s = bench.iloc[i], bench_sma.iloc[i]
            now_on = bool(pd.isna(s) or b > s)
            if market_on and not now_on:
                for t in list(pos):
                    close(t, i, "market_off")
            if now_on and not market_on:
                force_reb = True
            market_on = now_on

        if force_reb or last_reb is None or i - last_reb >= o.rebalance_every_days:
            last_reb = i
            if market_on:
                ranked = _ranked(prices, i, o, cols)
                target = _select(ranked, set(pos), o)
                for t in list(pos):
                    if t not in target:
                        close(t, i, "rotate")
                exposure = 1.0
                if o.vol_target and target:
                    r = prices.iloc[i - o.vol_lookback_days:i + 1][target].pct_change().iloc[1:].mean(axis=1)
                    vol = float(r.std() * np.sqrt(252))
                    if vol > 0:
                        exposure = min(1.0, o.vol_target / vol)
                total = value(i)
                slot = total * exposure / o.max_positions
                for t in target:
                    if t not in pos:
                        open_(t, i, slot)
        equity[dates[i]] = value(i)

    eq = pd.Series(equity)
    bm = o.starting_capital * bench.loc[eq.index[0]:eq.index[-1]] / bench.loc[eq.index[0]]
    return {"equity": eq, "bench": bm.ffill(), "trades": trades}


def stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return {
        "cagr": (eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1 if yrs > 0 else np.nan,
        "max_dd": float((eq / eq.cummax() - 1).min()),
        "sharpe": float(r.mean() / r.std() * np.sqrt(252)) if r.std() > 0 else np.nan,
    }

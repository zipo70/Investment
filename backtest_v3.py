"""
backtest_v3.py — realistisk backtest af swing-modellerne (2026-10-25).

Svar på analytiker-reviewet. Forskelle fra backtest.py:
  * Signal på dagens lukkekurs, handel til NÆSTE handelsdags åbningskurs.
  * Alt regnes i DKK: kurser omregnes med dagens valutakurs, og der betales
    vekselgebyr (default 0,25 %) på alle handler i fremmed valuta, oveni
    kurtage (0,10 %, min. 29 kr.).
  * Valgfrit sektorloft (maks K aktier fra samme sektor).
  * Kan teste APPENS tekniske model (core.compute_technical_signals +
    compute_long_term_signals, kombineret 60/40 som i kompositscoren) med
    appens KØB/SÆLG-regler — ikke kun den simple momentum-model.
  * Benchmarks: ACWI (globalt indeks) og MTUM (momentum-faktor-ETF), i DKK.

Popularitet, fundamental og Aktieguld kan IKKE testes: Yahoo har kun
dagens værdier, ikke historiske. De indgår derfor ikke.

Signaler beregnes én gang på et ugentligt gitter (hver 5. handelsdag) og
genbruges af alle modeller og startuger, så kørslen kan nå at blive færdig.

Research-værktøj, ikke finansiel rådgivning.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core import Config as CoreConfig, compute_technical_signals, compute_long_term_signals

START = "2014-01-01"
GRID_START = "2015-01-02"
MASTER = "ACWI"
BENCHES = ["ACWI", "MTUM"]
FX_PAIRS = {"USD": "USDDKK=X", "EUR": "EURDKK=X", "SEK": "SEKDKK=X", "NOK": "NOKDKK=X",
            "CHF": "CHFDKK=X", "GBP": "GBPDKK=X"}
SUFFIX_CCY = {".CO": "DKK", ".ST": "SEK", ".OL": "NOK", ".HE": "EUR", ".AS": "EUR", ".DE": "EUR",
              ".PA": "EUR", ".MC": "EUR", ".MI": "EUR", ".BR": "EUR", ".SW": "CHF", ".L": "GBP"}


def currency_of(t: str) -> str:
    for suf, c in SUFFIX_CCY.items():
        if t.endswith(suf):
            return c
    return "USD"


@dataclass(frozen=True)
class Model:
    name: str
    score: str = "mom6"            # mom6 | mom12 | tech
    n: int = 5
    rebalance_weeks: int | None = 13  # None = løbende (fyld ledige pladser hver uge)
    stop: float = 0.15
    sector_cap: int | None = None
    entry_filter: bool = False     # kræv sælg-score < 20 ved køb (appens KØB-regel)
    live_exit: bool = False        # sælg ved appens SÆLG (sælg-score ≥ 50 eller brudt trend)
    max_hold_weeks: int | None = None
    realistic: bool = True         # False = som backtest.py: samme dags lukkekurs, lokal valuta, intet vekselgebyr


@dataclass(frozen=True)
class Costs:
    capital: float = 100_000.0
    cost_pct: float = 0.0010
    min_fee: float = 29.0
    fx_fee: float = 0.0025


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def _download(t: str) -> pd.DataFrame | None:
    import yfinance as yf
    for attempt in range(3):
        try:
            df = yf.download(t, start=START, auto_adjust=True, progress=False, threads=False)
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df.index = pd.DatetimeIndex(df.index).tz_localize(None).normalize()
            return df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
        except Exception as e:
            print(f"  {t}: forsøg {attempt + 1} fejlede ({e})")
            time.sleep(2 * (attempt + 1))
    return None


def _sector(t: str) -> str:
    try:
        import yfinance as yf
        return yf.Ticker(t).info.get("sector") or f"Ukendt:{t}"
    except Exception:
        return f"Ukendt:{t}"


class Data:
    def __init__(self, tickers: list[str]):
        raw = {}
        for t in tickers + BENCHES:
            df = _download(t)
            if df is not None and len(df) > 400:
                raw[t] = df
            time.sleep(0.3)
        fx = {}
        for c, pair in FX_PAIRS.items():
            df = _download(pair)
            fx[c] = df["Close"] if df is not None else None
            time.sleep(0.3)

        self.master = raw[MASTER].index
        self.tickers = [t for t in tickers if t in raw]
        self.raw = raw
        cols = self.tickers + [b for b in BENCHES if b in raw]
        close = pd.DataFrame({t: raw[t]["Close"] for t in cols}).reindex(self.master).ffill(limit=5)
        opn = pd.DataFrame({t: raw[t]["Open"] for t in cols}).reindex(self.master)
        self.close, self.open = close, opn.fillna(close)
        fxdf = pd.DataFrame({c: s for c, s in fx.items() if s is not None})
        fxdf = fxdf.reindex(self.master.union(fxdf.index)).ffill().reindex(self.master)
        fxdf["DKK"] = 1.0
        self.ccy = {t: currency_of(t) for t in cols}
        self.fx = pd.DataFrame({t: fxdf[self.ccy[t]] for t in cols}).bfill()
        self.sector = {}
        for t in self.tickers:
            self.sector[t] = _sector(t)
            time.sleep(0.2)
        print(f"Data: {len(self.tickers)} aktier, {self.master[0].date()} → {self.master[-1].date()}")

    def build_signals(self):
        cfg = CoreConfig()
        g0 = int(self.master.searchsorted(pd.Timestamp(GRID_START)))
        self.grid = np.arange(g0, len(self.master) - 1, 5)
        G, T = len(self.grid), len(self.tickers)
        arr = {k: np.full((G, T), np.nan) for k in ("tech", "sell", "trend", "mom6", "mom12")}
        t0 = time.time()
        for j, t in enumerate(self.tickers):
            df = self.raw[t]
            dates = df.index.values
            c = df["Close"].to_numpy(float)
            for gi, mi in enumerate(self.grid):
                pos = int(np.searchsorted(dates, self.master[mi].to_datetime64(), side="right"))
                if pos < 300:
                    continue
                ta = compute_technical_signals(df.iloc[pos - 320:pos], cfg)
                if not ta.get("ok"):
                    continue
                lt = compute_long_term_signals(df.iloc[max(0, pos - 1260):pos], cfg)
                tc = 0.6 * ta["ta_score"] + 0.4 * lt["long_term_score"] if lt.get("ok") else ta["ta_score"]
                arr["tech"][gi, j] = tc
                arr["sell"][gi, j] = ta.get("sell_score", 0)
                arr["trend"][gi, j] = 1.0 if ta.get("trend_ok") else 0.0
                arr["mom6"][gi, j] = c[pos - 22] / c[pos - 127] - 1
                arr["mom12"][gi, j] = c[pos - 22] / c[pos - 253] - 1 if pos >= 253 else np.nan
            print(f"  signaler {j + 1}/{T} ({t}) — {time.time() - t0:.0f}s")
        self.sig = arr


# ---------------------------------------------------------------------------
# Motor
# ---------------------------------------------------------------------------

def run(data: Data, m: Model, costs: Costs, start: str, end: str | None,
        offset_weeks: int = 0, allowed: set | None = None) -> dict:
    master, grid = data.master, data.grid
    gdates = master[grid]
    lo = int(gdates.searchsorted(pd.Timestamp(start))) + offset_weeks
    hi = len(grid) if end is None else int(gdates.searchsorted(pd.Timestamp(end), side="right"))
    tix = {t: j for j, t in enumerate(data.tickers)}
    usable = [t for t in data.tickers if allowed is None or t in allowed]
    C, O = data.close.to_numpy(float), data.open.to_numpy(float)
    col = {t: k for k, t in enumerate(data.close.columns)}
    FX = data.fx.to_numpy(float) if m.realistic else np.ones_like(C)

    def px_dkk(t, i, kind):
        p = (O if kind == "open" else C)[i, col[t]]
        return p * FX[i, col[t]]

    def fee(n_dkk, t):
        f = max(n_dkk * costs.cost_pct, costs.min_fee)
        if m.realistic and data.ccy[t] != "DKK":
            f += n_dkk * costs.fx_fee
        return f

    cash, pos, trades, eq = costs.capital, {}, [], {}
    fees_total = 0.0

    def value(i, kind="close"):
        v = cash
        for t, p in pos.items():
            x = px_dkk(t, i, kind)
            if not np.isnan(x):
                v += p["shares"] * x
        return v

    for k in range(lo, hi):
        d = grid[k]
        e = d + 1 if m.realistic else d
        if e >= len(master):
            break
        kind = "open" if m.realistic else "close"
        eq[master[d]] = value(d)

        S = {key: data.sig[key][k] for key in data.sig}
        sells = {}
        for t, p in pos.items():
            c_loc = C[d, col[t]]
            if not np.isnan(c_loc) and c_loc / p["entry_local"] - 1 <= -m.stop:
                sells[t] = "stop"
            elif m.live_exit and (S["sell"][tix[t]] >= 50 or S["trend"][tix[t]] == 0):
                sells[t] = "sælg-signal"
            elif m.max_hold_weeks and k - p["k"] >= m.max_hold_weeks:
                sells[t] = "maks holdetid"

        is_reb = m.rebalance_weeks is None or (k - lo) % m.rebalance_weeks == 0
        buys = []
        if is_reb:
            score = S["tech"] if m.score == "tech" else S[m.score]
            cands = []
            for t in usable:
                j = tix[t]
                if np.isnan(score[j]) or S["trend"][j] != 1:
                    continue
                if m.entry_filter and S["sell"][j] >= 20:
                    continue
                cands.append((score[j], t))
            cands.sort(reverse=True)
            ranked = [t for _, t in cands]
            keep = [t for t in pos if t not in sells]
            if m.rebalance_weeks is not None:  # rotation: kun top N beholdes
                target = []
                for t in ranked:
                    if len(target) >= m.n:
                        break
                    if t in sells:
                        continue
                    if m.sector_cap and sum(data.sector[x] == data.sector[t] for x in target) >= m.sector_cap:
                        continue
                    target.append(t)
                for t in keep:
                    if t not in target:
                        sells[t] = "rotation"
                buys = [t for t in target if t not in pos]
            else:  # løbende: fyld ledige pladser
                held = list(keep)
                for t in ranked:
                    if len(held) >= m.n:
                        break
                    if t in held or t in pos or t in sells:
                        continue
                    if m.sector_cap and sum(data.sector[x] == data.sector[t] for x in held) >= m.sector_cap:
                        continue
                    held.append(t)
                    buys.append(t)

        for t, why in sells.items():
            x = px_dkk(t, e, kind)
            if np.isnan(x):
                continue
            p = pos.pop(t)
            n = p["shares"] * x
            f = fee(n, t)
            cash += n - f
            fees_total += f
            trades.append({"ticker": t, "pnl_dkk": n - f - p["cost_dkk"], "reason": why,
                           "weeks": k - p["k"], "exit": str(master[e].date())})

        if buys:
            slot = value(e, kind) / m.n
            for t in buys:
                x = px_dkk(t, e, kind)
                if np.isnan(x) or x <= 0:
                    continue
                n = min(slot, cash) / (1 + costs.cost_pct + (costs.fx_fee if m.realistic else 0))
                if n < costs.min_fee * 20:
                    continue
                f = fee(n, t)
                cash -= n + f
                fees_total += f
                pos[t] = {"shares": n / x, "entry_local": (O if m.realistic else C)[e, col[t]],
                          "k": k, "cost_dkk": n + f}

    eq = pd.Series(eq)
    out = {"equity": eq, "trades": trades, "fees": fees_total, "open": list(pos)}
    for b in BENCHES:
        if b in col:
            s = pd.Series(C[:, col[b]] * (data.fx[b].to_numpy(float) if m.realistic else 1.0),
                          index=master).reindex(eq.index)
            out[b] = costs.capital * s / s.iloc[0]
    return out


def stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    if len(eq) < 10:
        return {"cagr": np.nan, "dd": np.nan, "sharpe": np.nan}
    yrs = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    return {"cagr": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100,
            "dd": float((eq / eq.cummax() - 1).min() * 100),
            "sharpe": float(r.mean() / r.std() * np.sqrt(52)) if r.std() > 0 else np.nan}

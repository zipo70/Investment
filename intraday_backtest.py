"""
intraday_backtest.py — backtest af en intradag-strategi med 1.000 kr./dag-loft.

Spørgsmålet (Jacobi, 2026-10-25): kan 100.000 kr. tjene ca. 1.000 kr. om
dagen ved at gå ind og ud af aktier samme dag, globalt, med swing-modellen
som filter? Dette script svarer på det med historiske data, FØR noget
kobles på Saxo.

STRATEGI (alle regler står i IntradayConfig nedenfor):
  1. Udvælgelse (dagligt, ingen kig ind i fremtiden): kun aktier hvor den
     eksisterende tekniske model (core.compute_technical_signals) på data
     til og med GÅRSDAGEN giver optrend (over 200-dages snit) og lav
     sælg-score. De bedste efter ta_score kommer på dagens liste.
     (Fundamental-, popularitets- og Aktieguld-scoren kan IKKE backtestes —
     Yahoo har kun dagens værdier, ikke historiske. Se forbehold i rapporten.)
  2. Indgang: opening range breakout. Åbningsintervallet = første 60 min
     (timedata) / 30 min (5-min-data). Køb ved næste bars åbning, når en bar
     lukker over intervallets top OG over dagens VWAP.
  3. Stop = bunden af åbningsintervallet. Kursmål = 2 × risikoen.
  4. Positionsstørrelse: maks 500 kr. risiko pr. handel, maks 1/3 af
     kapitalen pr. position, maks 3 handler pr. dag.
  5. Dagsregler: ingen nye handler når dagens realiserede resultat er
     ≥ +1.000 kr. eller ≤ −1.000 kr.
  6. Exit senest ved børsens lukning (hver aktie på sin egen børs).
     Undtagelse (kan slås til/fra): position i plus, der lukker i toppen af
     dagens interval og stadig er godkendt af dagsmodellen, må holdes én
     nat — med stop flyttet til indgangskursen.
  7. Omkostninger: kurtage + spread trækkes pr. handel (begge veje).

Kør:  python intraday_backtest.py      (kræver Yahoo-adgang — køres på GitHub)
Output: output/intraday_backtest.md, .json og output/intraday_trades.csv

Research-værktøj, ikke finansiel rådgivning.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import os
import time
from dataclasses import dataclass, asdict, replace

import numpy as np
import pandas as pd

from core import Config, compute_technical_signals, fetch_ticker_df, full_universe, region_of, BENCHMARK

OUT_DIR = "output"
CPH = "Europe/Copenhagen"


@dataclass(frozen=True)
class IntradayConfig:
    capital_dkk: float = 100_000
    daily_target_dkk: float = 1_000      # stop for dagen ved denne gevinst
    daily_loss_limit_dkk: float = 1_000  # stop for dagen ved dette tab
    risk_per_trade_dkk: float = 500
    max_position_frac: float = 1 / 3
    max_trades_per_day: int = 3
    candidates_per_day: int = 10         # top N fra dagsmodellen må handles
    max_sell_score: int = 20             # som KØB-reglen i compute_recommendation
    reward_risk: float = 2.0
    min_risk_pct: float = 0.003          # spring over hvis stop er < 0,3 % væk
    max_risk_pct: float = 0.03           # ... eller > 3 % væk
    cost_per_side: float = 0.0010        # 0,10 % kurtage+spread pr. vej
    allow_overnight: bool = True
    overnight_top_of_range: float = 0.8  # lukker i øverste 20 % af dagens interval


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_intraday(ticker: str, interval: str, period: str) -> pd.DataFrame | None:
    import yfinance as yf
    for attempt in range(3):
        try:
            df = yf.download(ticker, interval=interval, period=period, auto_adjust=False,
                             progress=False, threads=False, prepost=False)
            if df is None or df.empty:
                return None
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Open", "Close"])
            idx = df.index
            if idx.tz is None:
                idx = idx.tz_localize("UTC")
            df.index = idx
            # Handelsdag = børsens lokale dato (yfinance leverer børsens tidszone)
            df["session"] = [ts.date() for ts in df.index]
            return df
        except Exception as e:  # netværk/rate-limit
            print(f"  {ticker} {interval}: forsøg {attempt + 1} fejlede ({e})")
            time.sleep(2 * (attempt + 1))
    return None


def daily_eligibility(daily: pd.DataFrame, sessions, cfg: Config, icfg: IntradayConfig) -> dict:
    """{session_date: ta_score} for dage hvor aktien er godkendt, beregnet på
    dagsdata til og med dagen FØR (ingen look-ahead)."""
    idx = pd.DatetimeIndex(daily.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    dates = idx.values.astype("datetime64[D]")
    out = {}
    for s in sessions:
        pos = int(np.searchsorted(dates, np.datetime64(s, "D"), side="left"))  # rækker strengt før s
        if pos < 300:
            continue
        window = daily.iloc[max(0, pos - 320):pos]
        ta = compute_technical_signals(window, cfg)
        if ta.get("ok") and ta.get("trend_ok") and ta.get("sell_score", 100) < icfg.max_sell_score:
            out[s] = float(ta["ta_score"])
    return out


# ---------------------------------------------------------------------------
# Signal + handel for én aktie på én dag
# ---------------------------------------------------------------------------

def simulate_day(day: pd.DataFrame, next_day: pd.DataFrame | None, or_bars: int,
                 icfg: IntradayConfig, eligible_next: bool):
    """Returnerer én handel (dict) eller None."""
    if len(day) < or_bars + 2:
        return None
    o, h, l, c, v = (day[k].to_numpy(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    or_high, or_low = h[:or_bars].max(), l[:or_bars].min()
    typical = (h + l + c) / 3
    cumv = np.cumsum(np.nan_to_num(v))
    vwap = np.where(cumv > 0, np.cumsum(typical * np.nan_to_num(v)) / np.maximum(cumv, 1e-9), c)

    sig = None
    for i in range(or_bars, len(day) - 1):
        if c[i] > or_high and c[i] > vwap[i]:
            sig = i
            break
    if sig is None:
        return None

    e = sig + 1
    entry = o[e]
    stop = or_low
    if entry <= stop:
        return None
    risk_pct = (entry - stop) / entry
    if not (icfg.min_risk_pct <= risk_pct <= icfg.max_risk_pct):
        return None
    target = entry + icfg.reward_risk * (entry - stop)

    exit_px, reason, exit_ts = None, None, None
    for j in range(e, len(day)):
        if j > e and o[j] <= stop:            # gap under stop
            exit_px, reason = o[j], "stop (gap)"
        elif l[j] <= stop:                      # konservativt: stop før mål i samme bar
            exit_px, reason = stop, "stop"
        elif h[j] >= target:
            exit_px, reason = target, "mål"
        if exit_px is not None:
            exit_ts = day.index[j]
            break

    overnight = False
    if exit_px is None:
        last_c = c[-1]
        rng = h.max() - l.min()
        top = rng > 0 and (last_c - l.min()) / rng >= icfg.overnight_top_of_range
        if (icfg.allow_overnight and last_c > entry and top and eligible_next
                and next_day is not None and len(next_day) > 0):
            overnight = True
            be_stop = entry
            no, nh, nl, nc = (next_day[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
            for j in range(len(next_day)):
                if no[j] <= be_stop:
                    exit_px, reason = no[j], "nat: stop (gap)"
                elif nl[j] <= be_stop:
                    exit_px, reason = be_stop, "nat: stop"
                elif nh[j] >= target:
                    exit_px, reason = target, "nat: mål"
                if exit_px is not None:
                    exit_ts = next_day.index[j]
                    break
            if exit_px is None:
                exit_px, reason, exit_ts = nc[-1], "nat: lukning", next_day.index[-1]
        else:
            exit_px, reason, exit_ts = last_c, "lukning", day.index[-1]

    return {
        "entry_ts": day.index[e], "exit_ts": exit_ts, "entry": entry, "exit": exit_px,
        "stop": stop, "target": target, "risk_pct": risk_pct, "reason": reason, "overnight": overnight,
    }


# ---------------------------------------------------------------------------
# Portefølje: dagsregler på tværs af alle børser
# ---------------------------------------------------------------------------

def run_portfolio(raw_trades: list, icfg: IntradayConfig):
    by_day = {}
    for t in raw_trades:
        d = t["entry_ts"].tz_convert(CPH).date()
        by_day.setdefault(d, []).append(t)

    taken = []
    for d in sorted(by_day):
        cands = sorted(by_day[d], key=lambda t: (t["entry_ts"], -t["ta_score"]))
        open_pos, realized, n = [], 0.0, 0
        for t in cands:
            if n >= icfg.max_trades_per_day:
                break
            # luk positioner der er afsluttet før denne indgang
            still = []
            for p in open_pos:
                if p["exit_ts"] <= t["entry_ts"]:
                    realized += p["pnl_dkk"]
                else:
                    still.append(p)
            open_pos = still
            if realized >= icfg.daily_target_dkk or realized <= -icfg.daily_loss_limit_dkk:
                break
            if any(p["ticker"] == t["ticker"] for p in open_pos):
                continue
            used = sum(p["notional"] for p in open_pos)
            notional = min(icfg.risk_per_trade_dkk / t["risk_pct"],
                           icfg.capital_dkk * icfg.max_position_frac,
                           icfg.capital_dkk - used)
            if notional < 1_000:
                continue
            gross = notional * (t["exit"] / t["entry"] - 1)
            cost = notional * icfg.cost_per_side * 2
            row = dict(t, notional=notional, pnl_dkk=gross - cost, cost_dkk=cost, day=d)
            open_pos.append(row)
            taken.append(row)
            n += 1
    return taken


def metrics(trades: list, all_days: list, icfg: IntradayConfig) -> dict:
    if not trades:
        return {"handler": 0}
    df = pd.DataFrame(trades)
    daily = df.groupby("day")["pnl_dkk"].sum().reindex(all_days, fill_value=0.0)
    equity = icfg.capital_dkk + daily.cumsum()
    dd = (equity / equity.cummax() - 1).min() * 100
    wins, losses = df.loc[df.pnl_dkk > 0, "pnl_dkk"], df.loc[df.pnl_dkk <= 0, "pnl_dkk"]
    n_days = len(all_days)
    total = float(daily.sum())
    years = n_days / 252
    return {
        "handelsdage": n_days,
        "handler": len(df),
        "dage_med_handel": int((df.groupby("day").size() > 0).sum()),
        "samlet_dkk": round(total),
        "gns_pr_dag_dkk": round(total / n_days, 1),
        "aarligt_afkast_pct": round(((equity.iloc[-1] / icfg.capital_dkk) ** (1 / years) - 1) * 100, 1)
        if years > 0 and equity.iloc[-1] > 0 else None,
        "dage_over_1000_pct": round((daily >= icfg.daily_target_dkk).mean() * 100, 1),
        "dage_i_plus_pct": round((daily > 0).mean() * 100, 1),
        "dage_i_minus_pct": round((daily < 0).mean() * 100, 1),
        "bedste_dag_dkk": round(float(daily.max())),
        "vaerste_dag_dkk": round(float(daily.min())),
        "max_drawdown_pct": round(float(dd), 1),
        "hitrate_pct": round(len(wins) / len(df) * 100, 1),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
        "gns_gevinst_dkk": round(float(wins.mean()), 0) if len(wins) else None,
        "gns_tab_dkk": round(float(losses.mean()), 0) if len(losses) else None,
        "omkostninger_dkk": round(float(df.cost_dkk.sum())),
        "natten_over": int(df.overnight.sum()),
        "pr_region": {k: round(float(v)) for k, v in df.groupby("region")["pnl_dkk"].sum().items()},
    }


# ---------------------------------------------------------------------------
# Kørsel
# ---------------------------------------------------------------------------

def build_raw_trades(data: dict, elig: dict, or_bars: int, icfg: IntradayConfig) -> list:
    raw = []
    # dagens top-N kandidater pr. dato på tværs af universet
    by_date = {}
    for tk, e in elig.items():
        for d, s in e.items():
            by_date.setdefault(d, []).append((s, tk))
    allowed = {d: {tk for _, tk in sorted(v, reverse=True)[: icfg.candidates_per_day]}
               for d, v in by_date.items()}

    for tk, df in data.items():
        sessions = sorted(df["session"].unique())
        groups = {s: g for s, g in df.groupby("session")}
        for k, s in enumerate(sessions):
            if tk not in allowed.get(s, ()):
                continue
            nxt = sessions[k + 1] if k + 1 < len(sessions) else None
            t = simulate_day(groups[s], groups.get(nxt) if nxt else None, or_bars, icfg,
                             eligible_next=bool(nxt and tk in allowed.get(nxt, ())))
            if t:
                t.update(ticker=tk, region=region_of(tk), ta_score=elig[tk][s], session=s)
                raw.append(t)
    return raw


def benchmark_pct(data_first, data_last) -> float | None:
    df, _ = fetch_ticker_df(BENCHMARK, period="5y")
    if df is None:
        return None
    c = df["Close"].dropna()
    c.index = pd.to_datetime(c.index).date
    c = c[(c.index >= data_first) & (c.index <= data_last)]
    return round(float(c.iloc[-1] / c.iloc[0] - 1) * 100, 1) if len(c) > 1 else None


def run_dataset(label, interval, period, or_bars, universe, daily_cache, cfg, scenarios):
    print(f"\n=== {label}: henter {interval}-data ({period}) ===")
    data = {}
    for tk in universe:
        df = load_intraday(tk, interval, period)
        if df is not None and len(df) > 50:
            data[tk] = df
        time.sleep(0.3)
    print(f"  {len(data)}/{len(universe)} tickere med data")

    elig = {}
    base = scenarios[0][1]
    for tk, df in data.items():
        daily = daily_cache.get(tk)
        if daily is None:
            continue
        elig[tk] = daily_eligibility(daily, sorted(df["session"].unique()), cfg, base)
    all_days = sorted({ts.tz_convert(CPH).date() for df in data.values() for ts in df.index
                       if ts.tz_convert(CPH).weekday() < 5})

    results, trades_out = {}, []
    for name, icfg in scenarios:
        raw = build_raw_trades(data, elig, or_bars, icfg)
        taken = run_portfolio(raw, icfg)
        results[name] = metrics(taken, all_days, icfg)
        if name == scenarios[0][0]:
            trades_out = taken
        print(f"  {name}: {results[name]}")
    period_info = {"fra": str(all_days[0]) if all_days else None, "til": str(all_days[-1]) if all_days else None,
                   "tickere": len(data),
                   "acwi_pct": benchmark_pct(all_days[0], all_days[-1]) if all_days else None}
    return results, trades_out, period_info


def main():
    cfg = Config()
    base = IntradayConfig()
    scenarios = [
        ("Basis (0,10 % pr. vej, nat tilladt)", base),
        ("Uden natten over", replace(base, allow_overnight=False)),
        ("Lave omkostninger (0,05 %)", replace(base, cost_per_side=0.0005)),
        ("Høje omkostninger (0,15 %)", replace(base, cost_per_side=0.0015)),
        ("Uden 1.000 kr.-loft", replace(base, daily_target_dkk=1e12)),
    ]
    universe = [t for t in full_universe() if t != BENCHMARK]

    print("Henter dagsdata til udvælgelsesfilteret...")
    daily_cache = {}
    for tk in universe:
        df, _ = fetch_ticker_df(tk, period="5y")
        if df is not None:
            daily_cache[tk] = df
        time.sleep(0.3)

    out = {"genereret_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "config": asdict(base), "datasaet": {}}
    trades_all = []
    for label, interval, period, or_bars in (
        ("Timedata, ~2 år", "60m", "730d", 1),
        ("5-min-data, ~60 dage", "5m", "60d", 6),
    ):
        res, trades, info = run_dataset(label, interval, period, or_bars, universe, daily_cache, cfg, scenarios)
        out["datasaet"][label] = {"periode": info, "scenarier": res}
        for t in trades:
            trades_all.append({**{k: (str(v) if isinstance(v, (pd.Timestamp, dt.date)) else v)
                                  for k, v in t.items()}, "datasaet": label})

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "intraday_backtest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    pd.DataFrame(trades_all).to_csv(os.path.join(OUT_DIR, "intraday_trades.csv"), index=False)

    md = [f"# Intradag-backtest — {out['genereret_utc']}", "",
          "Kapital 100.000 kr. · risiko 500 kr./handel · maks 3 handler/dag · stop for dagen ved ±1.000 kr.", ""]
    for label, block in out["datasaet"].items():
        p = block["periode"]
        md += [f"## {label} ({p['fra']} → {p['til']}, {p['tickere']} aktier, ACWI {p['acwi_pct']}%)", "",
               "| Scenarie | Kr./dag | Årligt | Dage ≥1.000 | Dage i minus | Max DD | Hitrate | Handler |",
               "|---|---|---|---|---|---|---|---|"]
        for name, m in block["scenarier"].items():
            if not m.get("handler"):
                md.append(f"| {name} | – | – | – | – | – | – | 0 |")
                continue
            md.append(f"| {name} | {m['gns_pr_dag_dkk']} | {m['aarligt_afkast_pct']}% | "
                      f"{m['dage_over_1000_pct']}% | {m['dage_i_minus_pct']}% | {m['max_drawdown_pct']}% | "
                      f"{m['hitrate_pct']}% | {m['handler']} |")
        md.append("")
    md += ["Forbehold: kun teknisk filter (fundamental/popularitet/Aktieguld findes ikke historisk); "
           "dagens univers bruges bagud (overlevelses-bias); timedata er grov for intradag; "
           "ingen glidning ud over omkostningssatsen. Research, ikke rådgivning."]
    with open(os.path.join(OUT_DIR, "intraday_backtest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()

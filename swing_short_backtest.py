"""
swing_short_backtest.py — kort swing-variant af intradag-strategien.

Baggrund (2026-10-25): intraday_backtest.py viste at intradag-handel taber
(kurtage + breakouts der ikke holder samme dag), mens handler holdt natten
over var den eneste del i plus. Denne variant beholder SAMME indgang, men
holder positionen i op til N handelsdage med trailing stop i stedet for at
lukke ved dagens slutning.

Regler (SwingShortConfig):
  1. Udvælgelse: samme dagsfilter som intradag (core.compute_technical_signals
     på data til og med gårsdagen: optrend + lav sælg-score, top 10 pr. dag).
  2. Indgang: opening range breakout over åbningsintervallets top + over VWAP,
     køb ved næste bars åbning.
  3. Startstop: bunden af åbningsintervallet (R = indgang − stop).
  4. Når kursen har været +1R: stop flyttes til indgangskursen.
     Derefter trailing stop = højeste kurs siden indgang − 2R.
  5. Exit: stop, ellers ved lukning på dag N (default 5).
  6. Maks 3 åbne positioner, 500 kr. risiko pr. handel, maks 1/3 kapital pr. position.
  7. Omkostninger 0,10 % pr. vej (scenarier med 0,05 % og 0,15 %).

Kør:  python swing_short_backtest.py   (kræver Yahoo — køres på GitHub)
Output: output/swing_short_backtest.md, .json og output/swing_short_trades.csv

Research-værktøj, ikke finansiel rådgivning.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import time
from dataclasses import dataclass, asdict, replace

import numpy as np
import pandas as pd

from core import Config, fetch_ticker_df, full_universe, region_of, BENCHMARK
from intraday_backtest import (IntradayConfig, load_intraday, daily_eligibility, benchmark_pct, CPH)

OUT_DIR = "output"


@dataclass(frozen=True)
class SwingShortConfig:
    capital_dkk: float = 100_000
    risk_per_trade_dkk: float = 500
    max_open_positions: int = 3
    max_new_per_day: int = 3
    candidates_per_day: int = 10
    min_risk_pct: float = 0.003
    max_risk_pct: float = 0.03
    max_hold_days: int = 5
    breakeven_at_r: float = 1.0
    trail_r: float = 2.0
    cost_per_side: float = 0.0010
    daily_target_dkk: float = 1_000      # kun til statistik ("dage ≥ 1.000 kr.")


def simulate_trade(sessions_df: list, or_bars: int, scfg: SwingShortConfig):
    """sessions_df[0] = indgangsdagen, resten = efterfølgende handelsdage."""
    day = sessions_df[0]
    if len(day) < or_bars + 2:
        return None
    o, h, l, c, v = (day[k].to_numpy(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    or_high, or_low = h[:or_bars].max(), l[:or_bars].min()
    typical = (h + l + c) / 3
    vv = np.nan_to_num(v)
    cumv = np.cumsum(vv)
    vwap = np.where(cumv > 0, np.cumsum(typical * vv) / np.maximum(cumv, 1e-9), c)
    sig = next((i for i in range(or_bars, len(day) - 1) if c[i] > or_high and c[i] > vwap[i]), None)
    if sig is None:
        return None
    e = sig + 1
    entry, stop0 = o[e], or_low
    if entry <= stop0:
        return None
    risk_pct = (entry - stop0) / entry
    if not (scfg.min_risk_pct <= risk_pct <= scfg.max_risk_pct):
        return None
    R = entry - stop0

    bars = pd.concat([day.iloc[e:]] + list(sessions_df[1:scfg.max_hold_days]))
    bo, bh, bl, bc = (bars[k].to_numpy(float) for k in ("Open", "High", "Low", "Close"))
    stop, peak = stop0, entry
    for j in range(len(bars)):
        if j > 0 and bo[j] <= stop:
            return _trade(day, e, bars, j, entry, bo[j], stop0, risk_pct, "stop (gap)")
        if bl[j] <= stop:
            return _trade(day, e, bars, j, entry, stop, stop0, risk_pct,
                          "trailing stop" if stop > entry else ("break-even" if stop == entry else "stop"))
        peak = max(peak, bh[j])
        if peak >= entry + scfg.breakeven_at_r * R:
            stop = max(stop, entry, peak - scfg.trail_r * R)
    reason = "maks holdetid" if len(sessions_df) >= scfg.max_hold_days else "data slut"
    return _trade(day, e, bars, len(bars) - 1, entry, bc[-1], stop0, risk_pct, reason)


def _trade(day, e, bars, j, entry, exit_px, stop0, risk_pct, reason):
    return {"entry_ts": day.index[e], "exit_ts": bars.index[j], "entry": entry, "exit": exit_px,
            "stop": stop0, "risk_pct": risk_pct, "reason": reason,
            "hold_sessions": len({ts.date() for ts in bars.index[: j + 1]})}


def build_raw(data, elig, or_bars, scfg):
    by_date = {}
    for tk, e in elig.items():
        for d, s in e.items():
            by_date.setdefault(d, []).append((s, tk))
    allowed = {d: {tk for _, tk in sorted(v, reverse=True)[: scfg.candidates_per_day]} for d, v in by_date.items()}
    raw = []
    for tk, df in data.items():
        sessions = sorted(df["session"].unique())
        groups = {s: g for s, g in df.groupby("session")}
        for k, s in enumerate(sessions):
            if tk not in allowed.get(s, ()):
                continue
            seq = [groups[x] for x in sessions[k:k + scfg.max_hold_days]]
            t = simulate_trade(seq, or_bars, scfg)
            if t:
                t.update(ticker=tk, region=region_of(tk), ta_score=elig[tk][s])
                raw.append(t)
    return raw


def run_portfolio(raw, scfg):
    taken, open_pos, new_per_day = [], [], {}
    for t in sorted(raw, key=lambda t: (t["entry_ts"], -t["ta_score"])):
        open_pos = [p for p in open_pos if p["exit_ts"] > t["entry_ts"]]
        d = t["entry_ts"].tz_convert(CPH).date()
        if new_per_day.get(d, 0) >= scfg.max_new_per_day or len(open_pos) >= scfg.max_open_positions:
            continue
        if any(p["ticker"] == t["ticker"] for p in open_pos):
            continue
        used = sum(p["notional"] for p in open_pos)
        notional = min(scfg.risk_per_trade_dkk / t["risk_pct"], scfg.capital_dkk / scfg.max_open_positions,
                       scfg.capital_dkk - used)
        if notional < 1_000:
            continue
        cost = notional * scfg.cost_per_side * 2
        row = dict(t, notional=notional, cost_dkk=cost,
                   pnl_dkk=notional * (t["exit"] / t["entry"] - 1) - cost,
                   exit_day=t["exit_ts"].tz_convert(CPH).date())
        open_pos.append(row)
        taken.append(row)
        new_per_day[d] = new_per_day.get(d, 0) + 1
    return taken


def metrics(trades, all_days, scfg):
    if not trades:
        return {"handler": 0}
    df = pd.DataFrame(trades)
    daily = df.groupby("exit_day")["pnl_dkk"].sum().reindex(all_days, fill_value=0.0)
    equity = scfg.capital_dkk + daily.cumsum()
    years = len(all_days) / 252
    wins, losses = df.pnl_dkk[df.pnl_dkk > 0], df.pnl_dkk[df.pnl_dkk <= 0]
    end = float(equity.iloc[-1])
    return {
        "handelsdage": len(all_days), "handler": len(df),
        "samlet_dkk": round(float(daily.sum())),
        "gns_pr_dag_dkk": round(float(daily.mean()), 1),
        "aarligt_afkast_pct": round(((end / scfg.capital_dkk) ** (1 / years) - 1) * 100, 1) if end > 0 and years > 0 else -100.0,
        "dage_over_1000_pct": round(float((daily >= scfg.daily_target_dkk).mean() * 100), 1),
        "dage_i_minus_pct": round(float((daily < 0).mean() * 100), 1),
        "max_drawdown_pct": round(float((equity / equity.cummax() - 1).min() * 100), 1),
        "hitrate_pct": round(len(wins) / len(df) * 100, 1),
        "profit_factor": round(float(wins.sum() / -losses.sum()), 2) if losses.sum() < 0 else None,
        "gns_gevinst_dkk": round(float(wins.mean())) if len(wins) else None,
        "gns_tab_dkk": round(float(losses.mean())) if len(losses) else None,
        "gns_holdetid_dage": round(float(df.hold_sessions.mean()), 1),
        "omkostninger_dkk": round(float(df.cost_dkk.sum())),
        "brutto_foer_omk_dkk": round(float((df.pnl_dkk + df.cost_dkk).sum())),
        "exit_aarsager": {k: int(v) for k, v in df.reason.value_counts().items()},
        "pr_region": {k: round(float(v)) for k, v in df.groupby("region")["pnl_dkk"].sum().items()},
        "pr_aar": {str(k): round(float(v)) for k, v in
                   df.groupby(pd.to_datetime(df.exit_day).dt.year)["pnl_dkk"].sum().items()},
    }


def main():
    cfg = Config()
    base = SwingShortConfig()
    scenarios = [
        ("Basis: maks 5 dage, 0,10 %", base),
        ("Maks 3 dage", replace(base, max_hold_days=3)),
        ("Maks 2 dage", replace(base, max_hold_days=2)),
        ("Maks 10 dage", replace(base, max_hold_days=10)),
        ("Lave omkostninger (0,05 %)", replace(base, cost_per_side=0.0005)),
        ("Høje omkostninger (0,15 %)", replace(base, cost_per_side=0.0015)),
    ]
    elig_cfg = IntradayConfig()
    universe = [t for t in full_universe() if t != BENCHMARK]

    print("Henter dagsdata + timedata...")
    data, elig = {}, {}
    for tk in universe:
        daily, _ = fetch_ticker_df(tk, period="5y")
        hourly = load_intraday(tk, "60m", "730d")
        time.sleep(0.3)
        if daily is None or hourly is None or len(hourly) < 50:
            continue
        data[tk] = hourly
        elig[tk] = daily_eligibility(daily, sorted(hourly["session"].unique()), cfg, elig_cfg)
    all_days = sorted({ts.tz_convert(CPH).date() for df in data.values() for ts in df.index
                       if ts.tz_convert(CPH).weekday() < 5})
    print(f"{len(data)} aktier, {all_days[0]} → {all_days[-1]}")

    results, first_trades = {}, []
    for name, scfg in scenarios:
        taken = run_portfolio(build_raw(data, elig, 1, scfg), scfg)
        results[name] = metrics(taken, all_days, scfg)
        if not first_trades:
            first_trades = taken
        print(name, results[name])

    period = {"fra": str(all_days[0]), "til": str(all_days[-1]), "tickere": len(data),
              "acwi_pct": benchmark_pct(all_days[0], all_days[-1])}
    out = {"genereret_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
           "config": asdict(base), "periode": period, "scenarier": results}
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "swing_short_backtest.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    pd.DataFrame(first_trades).to_csv(os.path.join(OUT_DIR, "swing_short_trades.csv"), index=False)

    md = [f"# Kort swing-backtest — {out['genereret_utc']}", "",
          f"Timedata {period['fra']} → {period['til']}, {period['tickere']} aktier, ACWI {period['acwi_pct']}%. "
          "100.000 kr., 500 kr. risiko/handel, maks 3 åbne positioner, trailing stop.", "",
          "| Scenarie | Kr./dag | Årligt | Dage ≥1.000 | Max DD | Hitrate | Profit factor | Handler |",
          "|---|---|---|---|---|---|---|---|"]
    for name, m in results.items():
        md.append(f"| {name} | {m.get('gns_pr_dag_dkk')} | {m.get('aarligt_afkast_pct')}% | "
                  f"{m.get('dage_over_1000_pct')}% | {m.get('max_drawdown_pct')}% | {m.get('hitrate_pct')}% | "
                  f"{m.get('profit_factor')} | {m.get('handler')} |")
    md += ["", "Forbehold: kun teknisk filter; dagens univers bruges bagud (overlevelses-bias); "
           "realiseret resultat (ikke daglig markedsværdi). Research, ikke rådgivning."]
    with open(os.path.join(OUT_DIR, "swing_short_backtest.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()

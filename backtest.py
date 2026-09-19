"""
Porteføljesimulering (backtest-motor).

Simulerer dag-for-dag med to typer beslutningspunkter:

- Rebalancerings-dage (hver ~63 handelsdage / 1 kvartal): kør trend/momentum-
  rangering, luk positioner der ikke længere er i top-N, åbn nye i top-N.
  Positioner der beholdes fra forrige cyklus rebalanceres IKKE i vægt (mindre
  unødig omsætning/omkostning — realistisk swing trading-adfærd).
- Risikotjek-dage (hver ~5 handelsdage / ugentligt): tjek stop-loss på åbne
  positioner. Ved brud sælges positionen, og provenuet forbliver kontant til
  næste rebalancering (konfigurerbart via cfg.reinvest_after_stop).

Al handel eksekveres til dagens slutkurs (close) på beslutningsdagen selv —
en almindelig forsimpling i denne slags backtests. En mere konservativ
variant ville eksekvere til NÆSTE dags åbningskurs; det er en oplagt
udvidelse hvis du vil gøre simuleringen strengere.

VIGTIG BEGRÆNSNING — valuta: aktierne i universet handles i flere valutaer
(USD, EUR, DKK, SEK, NOK, CHF, GBP m.fl.). Denne motor regner porteføljens
værdi som en vægtet sum af hver akties LOKALE procentafkast — den konverterer
IKKE til en fælles valuta og medregner IKKE valutakursudsving eller Nordnets
vekslingsgebyr (typisk ~0,25-0,5% pr. handel i fremmed valuta). Til reel
handel skal FX-eksponering lægges ind som en ekstra omkostnings- og
risikofaktor.
"""

import pandas as pd
import numpy as np

from config import Config
from strategy import rank_candidates, target_weights
from universe import BENCHMARK


def _trade_cost(notional: float, cfg: Config) -> float:
    return max(abs(notional) * cfg.cost_pct, cfg.min_fee_local)


def run_backtest(prices_wide: pd.DataFrame, cfg: Config):
    """Run the swing strategy over the full price history.

    Returns a dict with:
      equity_curve: pd.Series (date -> total portfolio value)
      benchmark_curve: pd.Series (date -> benchmark value, same starting capital)
      trade_log: list of dicts (closed trades)
      holdings_log: list of dicts (rebalance snapshots, for the report)
    """
    dates = prices_wide.index
    universe_cols = [c for c in prices_wide.columns if c != BENCHMARK]

    needed = max(cfg.trend_sma_window, cfg.momentum_lookback_days) + 1
    if len(dates) <= needed:
        raise RuntimeError("Ikke nok historik til at starte backtesten.")

    start_i = needed
    cash = cfg.starting_capital
    positions = {}  # ticker -> {"shares": float, "entry_price": float, "entry_date": Timestamp}
    equity_curve = {}
    trade_log = []
    holdings_log = []

    last_rebalance_i = None
    last_risk_check_i = None

    def portfolio_value(i):
        val = cash
        px = prices_wide.iloc[i]
        for t, pos in positions.items():
            price = px.get(t, np.nan)
            if pd.notna(price):
                val += pos["shares"] * price
        return val

    def close_position(t, i, reason):
        nonlocal cash
        price = prices_wide.iloc[i][t]
        pos = positions.pop(t)
        notional = pos["shares"] * price
        fee = _trade_cost(notional, cfg)
        cash += notional - fee
        holding_days = (dates[i] - pos["entry_date"]).days
        ret_pct = (price / pos["entry_price"]) - 1.0
        trade_log.append({
            "ticker": t,
            "entry_date": pos["entry_date"],
            "exit_date": dates[i],
            "entry_price": pos["entry_price"],
            "exit_price": price,
            "return_pct": ret_pct,
            "holding_days": holding_days,
            "reason": reason,
        })

    def open_position(t, i, weight, total_value):
        nonlocal cash
        price = prices_wide.iloc[i][t]
        if pd.isna(price) or price <= 0:
            return
        target_notional = total_value * weight
        fee_estimate = _trade_cost(target_notional, cfg)
        spend = max(target_notional - fee_estimate, 0)
        shares = spend / price
        actual_notional = shares * price
        fee = _trade_cost(actual_notional, cfg)
        total_cost = actual_notional + fee
        if total_cost > cash:
            shares = cash / (price * (1 + cfg.cost_pct))
            actual_notional = shares * price
            fee = _trade_cost(actual_notional, cfg)
            total_cost = actual_notional + fee
        if shares <= 0:
            return
        cash -= total_cost
        positions[t] = {"shares": shares, "entry_price": price, "entry_date": dates[i]}

    for i in range(start_i, len(dates)):
        date = dates[i]

        # --- risk check (stop-loss) ---
        is_risk_day = (last_risk_check_i is None) or (i - last_risk_check_i >= cfg.risk_check_every_days)
        if is_risk_day:
            last_risk_check_i = i
            for t in list(positions.keys()):
                price = prices_wide.iloc[i].get(t, np.nan)
                if pd.isna(price):
                    continue
                entry = positions[t]["entry_price"]
                if (price / entry - 1.0) <= -cfg.stop_loss_pct:
                    close_position(t, i, reason="stop_loss")

        # --- rebalance ---
        is_rebal_day = (last_rebalance_i is None) or (i - last_rebalance_i >= cfg.rebalance_every_days)
        if is_rebal_day:
            last_rebalance_i = i
            candidates = rank_candidates(prices_wide[universe_cols], date, cfg, exclude=set())
            weights = target_weights(candidates, prices_wide, date, cfg)
            target_set = set(weights.index)

            # close positions no longer in target
            for t in list(positions.keys()):
                if t not in target_set:
                    close_position(t, i, reason="rebalance_rotate")

            # open new positions from target not currently held
            total_value = portfolio_value(i)
            new_names = [t for t in weights.index if t not in positions]
            for t in new_names:
                open_position(t, i, weights[t], total_value)

            holdings_log.append({
                "date": date,
                "holdings": list(positions.keys()),
                "candidates_considered": len(candidates),
                "portfolio_value": portfolio_value(i),
            })

        equity_curve[date] = portfolio_value(i)

    equity_curve = pd.Series(equity_curve).sort_index()

    # Benchmark: buy-and-hold from the same start date, same starting capital,
    # no costs (a clean reference line).
    bench_px = prices_wide[BENCHMARK].loc[equity_curve.index[0]:equity_curve.index[-1]]
    bench_curve = cfg.starting_capital * (bench_px / bench_px.iloc[0])

    return {
        "equity_curve": equity_curve,
        "benchmark_curve": bench_curve,
        "trade_log": trade_log,
        "holdings_log": holdings_log,
        "final_positions": positions,
        "final_cash": cash,
    }

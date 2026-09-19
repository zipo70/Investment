"""
Performance-nøgletal ud fra en equity-kurve og en handelslog.

Antagelser (vigtige at kende når tallene skal fortolkes):
- Sharpe-ratio antager 0% risikofri rente (forsimpling).
- CAGR beregnes ud fra kalenderdage mellem første og sidste dato.
- Alle afkast er FØR skat.
"""

import numpy as np
import pandas as pd


def cagr(equity: pd.Series) -> float:
    if len(equity) < 2:
        return float("nan")
    years = (equity.index[-1] - equity.index[0]).days / 365.25
    if years <= 0:
        return float("nan")
    total_return = equity.iloc[-1] / equity.iloc[0]
    return total_return ** (1 / years) - 1


def max_drawdown(equity: pd.Series):
    running_max = equity.cummax()
    dd = equity / running_max - 1.0
    trough_date = dd.idxmin()
    max_dd = dd.min()
    peak_date = equity.loc[:trough_date].idxmax()
    return max_dd, peak_date, trough_date


def sharpe_ratio(equity: pd.Series, periods_per_year: int = 252, rf: float = 0.0) -> float:
    daily_ret = equity.pct_change().dropna()
    if daily_ret.std() == 0 or len(daily_ret) < 2:
        return float("nan")
    excess = daily_ret - rf / periods_per_year
    return np.sqrt(periods_per_year) * excess.mean() / daily_ret.std()


def quarterly_returns(equity: pd.Series) -> pd.DataFrame:
    q = equity.resample("QE").last()
    q_ret = q.pct_change()
    df = pd.DataFrame({"portfolio_value": q, "return": q_ret})
    df = df.dropna(subset=["return"])
    return df


def trade_stats(trade_log: list) -> dict:
    if not trade_log:
        return {
            "n_trades": 0, "win_rate": float("nan"),
            "avg_return_pct": float("nan"), "avg_holding_days": float("nan"),
            "best_trade_pct": float("nan"), "worst_trade_pct": float("nan"),
        }
    df = pd.DataFrame(trade_log)
    wins = (df["return_pct"] > 0).sum()
    return {
        "n_trades": len(df),
        "win_rate": wins / len(df),
        "avg_return_pct": df["return_pct"].mean(),
        "avg_holding_days": df["holding_days"].mean(),
        "best_trade_pct": df["return_pct"].max(),
        "worst_trade_pct": df["return_pct"].min(),
    }


def summary(equity: pd.Series, benchmark: pd.Series, trade_log: list) -> dict:
    dd, peak_dt, trough_dt = max_drawdown(equity)
    bench_dd, _, _ = max_drawdown(benchmark)

    return {
        "start_date": equity.index[0],
        "end_date": equity.index[-1],
        "start_value": equity.iloc[0],
        "end_value": equity.iloc[-1],
        "total_return": equity.iloc[-1] / equity.iloc[0] - 1,
        "cagr": cagr(equity),
        "max_drawdown": dd,
        "max_drawdown_peak": peak_dt,
        "max_drawdown_trough": trough_dt,
        "sharpe": sharpe_ratio(equity),
        "bench_total_return": benchmark.iloc[-1] / benchmark.iloc[0] - 1,
        "bench_cagr": cagr(benchmark),
        "bench_max_drawdown": bench_dd,
        "bench_sharpe": sharpe_ratio(benchmark),
        **{f"trade_{k}": v for k, v in trade_stats(trade_log).items()},
    }

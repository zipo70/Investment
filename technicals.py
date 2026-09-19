"""
Tekniske signaler (TA) til kandidat-screeneren.

Alle funktioner arbejder på én akties historiske Close/Volume-serier og er
udelukkende "snapshot"-orienterede (til brug lige nu, ikke i en no-lookahead
backtest-løkke som strategy.py). Ingen eksterne afhængigheder ud over
pandas/numpy — RSI og MACD er implementeret i ren pandas, så vi undgår
tunge/kompilerede TA-biblioteker (vigtigt for iPhone/Colab-kompatibilitet).

Triggere der beregnes:
- Golden/death cross: SMA50 krydser SMA200.
- 52-ugers breakout: kursen sætter ny 52-ugers højeste.
- RSI-oversolgt-rebound: RSI vender op gennem 30.
- MACD-momentumskift: MACD-histogram vender fra negativt til positivt.
- Volumenspike: dagens volumen er unormalt højt ift. 20-dages gennemsnit.
"""

import pandas as pd
import numpy as np


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI (standard 14-perioders formel)."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0)  # neutral hvis ingen tab (ren opadgående serie)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def _recent_event(bool_series: pd.Series, lookback_days: int):
    """Return (happened, days_ago) — days_ago=0 betyder i dag. Ser kun på de
    seneste `lookback_days` værdier (ekskl. NaN)."""
    tail = bool_series.tail(lookback_days)
    hits = tail[tail == True]  # noqa: E712
    if hits.empty:
        return False, None
    last_idx = bool_series.index.get_loc(hits.index[-1])
    days_ago = (len(bool_series) - 1) - last_idx
    return True, days_ago


def compute_technical_signals(df: pd.DataFrame, cfg) -> dict:
    """df skal have kolonner Close/Volume (som fra data_fetch), nyeste dato sidst.
    Returnerer et dict med alle rådata + triggere + en TA-score (0-100)."""
    close = df["Close"].dropna()
    volume = df["Volume"].dropna() if "Volume" in df.columns else pd.Series(dtype=float)

    needed = max(cfg.trend_sma_window, 252) + 5
    if len(close) < needed:
        return {"ok": False, "reason": f"utilstrækkelig historik ({len(close)} dage)"}

    sma50 = sma(close, 50)
    sma200 = sma(close, cfg.trend_sma_window)
    last_price = float(close.iloc[-1])
    last_sma200 = float(sma200.iloc[-1])
    trend_ok = last_price > last_sma200

    # --- Golden/death cross ---
    cross_diff = sma50 - sma200
    cross_sign = np.sign(cross_diff.dropna())
    cross_flip = cross_sign != cross_sign.shift(1)
    golden_series = cross_flip & (cross_sign > 0)
    death_series = cross_flip & (cross_sign < 0)
    golden_hit, golden_days_ago = _recent_event(golden_series, cfg.cross_lookback_days)
    death_hit, death_days_ago = _recent_event(death_series, cfg.cross_lookback_days)

    # --- 52-ugers breakout ---
    prior_high_252 = close.shift(1).rolling(252, min_periods=200).max()
    breakout_series = close > prior_high_252
    breakout_hit, breakout_days_ago = _recent_event(breakout_series, cfg.high_lookback_days)

    # --- RSI oversolgt-rebound ---
    rsi_series = rsi(close, cfg.rsi_window)
    last_rsi = float(rsi_series.iloc[-1])
    rsi_cross_up = (rsi_series > 30) & (rsi_series.shift(1) <= 30)
    rsi_rebound_hit, rsi_rebound_days_ago = _recent_event(rsi_cross_up, cfg.momentum_shift_lookback_days)

    # --- MACD-momentumskift ---
    _, _, macd_hist = macd(close, cfg.macd_fast, cfg.macd_slow, cfg.macd_signal)
    macd_flip_up = (macd_hist > 0) & (macd_hist.shift(1) <= 0)
    macd_shift_hit, macd_shift_days_ago = _recent_event(macd_flip_up, cfg.momentum_shift_lookback_days)

    momentum_shift_hit = rsi_rebound_hit or macd_shift_hit
    momentum_shift_days_ago = min(
        [d for d in (rsi_rebound_days_ago, macd_shift_days_ago) if d is not None],
        default=None,
    )

    # --- Volumenspike ---
    avg_vol_20 = volume.rolling(cfg.volume_spike_window, min_periods=cfg.volume_spike_window).mean()
    last_volume = float(volume.iloc[-1]) if not volume.empty else float("nan")
    last_avg_vol = float(avg_vol_20.iloc[-1]) if not avg_vol_20.empty else float("nan")
    volume_ratio = (last_volume / last_avg_vol) if last_avg_vol and not np.isnan(last_avg_vol) and last_avg_vol > 0 else float("nan")
    volume_spike_hit = bool(volume_ratio and not np.isnan(volume_ratio) and volume_ratio >= cfg.volume_spike_threshold)

    # --- Komposit TA-score (0-100) ---
    # Trend er en "gate": ingen trend => resten af triggerne tæller ikke,
    # ligesom i backtest-strategien (vi tvinger ikke aktier ind i en nedtrend).
    triggers = []
    score = 0
    if trend_ok:
        score += 30
        if golden_hit:
            score += 25
            triggers.append(f"Golden cross for {golden_days_ago} handelsdag(e) siden")
        if breakout_hit:
            score += 25
            triggers.append(f"Nyt 52-ugers højeste for {breakout_days_ago} handelsdag(e) siden")
        if momentum_shift_hit:
            score += 15
            src = "RSI-rebound" if rsi_rebound_hit else "MACD-momentumskift"
            score_days = rsi_rebound_days_ago if rsi_rebound_hit else macd_shift_days_ago
            triggers.append(f"{src} for {score_days} handelsdag(e) siden")
        if volume_spike_hit:
            score += 5
            triggers.append(f"Volumenspike ({volume_ratio:.1f}x 20-dages gennemsnit)")
    else:
        triggers.append("Under 200-dages glidende gennemsnit — trendfilter IKKE bestået")
        if death_hit:
            triggers.append(f"Death cross for {death_days_ago} handelsdag(e) siden")

    return {
        "ok": True,
        "last_price": last_price,
        "sma50": float(sma50.iloc[-1]),
        "sma200": last_sma200,
        "trend_ok": bool(trend_ok),
        "golden_cross": golden_hit,
        "golden_cross_days_ago": golden_days_ago,
        "death_cross": death_hit,
        "death_cross_days_ago": death_days_ago,
        "breakout_52w": breakout_hit,
        "breakout_52w_days_ago": breakout_days_ago,
        "high_252w": float(close.tail(252).max()),
        "rsi": last_rsi,
        "momentum_shift": momentum_shift_hit,
        "momentum_shift_days_ago": momentum_shift_days_ago,
        "volume_ratio": volume_ratio,
        "volume_spike": volume_spike_hit,
        "ta_score": min(score, 100),
        "ta_triggers": triggers,
    }

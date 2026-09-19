"""
Strategi- og backtest-parametre. Alt her er bevidst samlet ét sted, så du kan
tune og genkøre uden at rode i strategilogikken.
"""

from dataclasses import dataclass, field


@dataclass
class Config:
    # --- Backtest-periode ---
    start_date: str = "2015-01-01"
    end_date: str = None  # None = i dag

    # --- Portefølje ---
    starting_capital: float = 100_000.0   # simuleret kapital (paper trading)
    max_positions: int = 5
    weighting: str = "equal"              # "equal" eller "inverse_vol"

    # --- Rebalancering (3 måneders cyklus) ---
    rebalance_every_days: int = 63        # ~1 handelskvartal

    # --- Trend/momentum-signal ---
    trend_sma_window: int = 200           # trendfilter: kurs > SMA(200)
    momentum_lookback_days: int = 126     # ~6 mdr. samlet lookback
    momentum_skip_days: int = 21          # spring seneste ~1 mdr. over
    # Momentum-score = afkast fra (t - lookback) til (t - skip).
    # "Skip-month"-konventionen er standard i akademisk momentumforskning
    # (Jegadeesh & Titman) og undgår kortsigtet reversal-støj.

    # --- Risikostyring mellem rebalanceringer ---
    stop_loss_pct: float = 0.15           # luk position ved -15% fra indgang
    risk_check_every_days: int = 5        # tjek stop-loss ugentligt
    reinvest_after_stop: bool = False     # hold kontant til næste rebalancering

    # --- Omkostninger (approksimeret — bekræft reelle Nordnet-satser) ---
    cost_pct: float = 0.0010              # 0,10% pr. handel (køb/salg hver for sig)
    min_fee_local: float = 29.0           # simpel min.-kurtage, lokal valuta-enhed

    # --- Øvrigt ---
    min_history_days: int = 220           # aktien skal have mindst denne historik
                                            # for at være valgbar (SMA200 + margin)

    # ========================================================================
    # Kandidat-screener (FA/TA/popularitet-rangering, se screener.py)
    # ========================================================================

    # --- Trin 1: teknisk shortlist (bygges af hele universet, ingen API-kald) ---
    screener_shortlist_size: int = 20     # kun de N bedste TA-kandidater går
                                            # videre til analytiker-/sentiment-opslag
                                            # (holder antal netværkskald nede)
    screener_top_n: int = 15              # antal kandidater i den endelige rapport

    # --- Komposit-vægtning (TA vs. analytiker/sentiment) ---
    screener_ta_weight: float = 0.5
    screener_sentiment_weight: float = 0.5

    # --- Tekniske triggere (technicals.py) ---
    rsi_window: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    cross_lookback_days: int = 10         # golden/death cross: se N seneste dage
    high_lookback_days: int = 5           # 52-ugers breakout: se N seneste dage
    momentum_shift_lookback_days: int = 5 # RSI-rebound/MACD-skift: se N seneste dage
    volume_spike_window: int = 20         # gennemsnitsvindue for "normal" volumen
    volume_spike_threshold: float = 2.0   # spike = volumen >= 2x 20-dages snit

    # --- Analytiker-/sentiment-kilder (sentiment.py) ---
    reddit_subreddits: list = field(default_factory=lambda: ["stocks", "investing", "wallstreetbets"])
    reddit_time_filter: str = "week"      # praw: "day"|"week"|"month"|"year"|"all"
    reddit_search_limit: int = 15         # maks opslag pr. subreddit pr. søgeterm
    sentiment_request_delay_sec: float = 1.0  # pause mellem API-kald pr. kandidat
                                                # (høflighed over for gratis/rate-limitede API'er)

    def __post_init__(self):
        if self.max_positions < 1:
            raise ValueError("max_positions skal være >= 1")
        if not (0 < self.cost_pct < 0.05):
            raise ValueError("cost_pct virker urealistisk")
        if self.screener_ta_weight < 0 or self.screener_sentiment_weight < 0:
            raise ValueError("screener-vægte skal være >= 0")

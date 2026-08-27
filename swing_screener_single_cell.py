"""
Swing trading-agent — ALT-I-ÉN Colab-celle (KANDIDAT-SCREENER).

Copy-paste hele denne fil ind i én tom celle i Google Colab og kør den.
Kræver at kursdata allerede er hentet (kør backtest-cellen først, eller kør
denne — den henter selv om cachen er tom).

Før du kører denne celle, kør først i en celle for sig:
  !pip install -q -U yfinance pandas numpy requests
  !pip install -q praw   # valgfrit — kun for Reddit-sentiment, se README

Efter kørsel: vis rapporten inline (i en ny celle):
  from IPython.display import HTML, display
  display(HTML(open("output/screener.html").read()))
"""

# ============================================================================
# Fra: swing_agent/universe.py
# ============================================================================
"""
Aktieunivers for swing trading-agenten.

Tickere er i Yahoo Finance-format (bruges af yfinance). Listen er en kurateret
udvælgelse af likvide large/mid-cap aktier på tværs af fire regioner. Formålet
er at give momentum-strategien et bredt, men håndterbart univers at rangere.

Bemærk: Dette er IKKE det samme som "alle aktier man kan handle på Nordnet" —
det er et repræsentativt udsnit. Nordnet-udbuddet er bredere (og varierer med
kontotype/marked), men de fleste af nedenstående kan handles direkte via
Nordnet på deres respektive hjemmemarkeder, evt. som ADR/depotbeviser for
enkelte EM-navne.
"""

US = [
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "ORCL",
    "CRM", "ADBE", "NFLX", "COST", "WMT", "HD", "PG", "KO", "PEP", "JNJ",
    "UNH", "PFE", "JPM", "BAC", "V", "MA", "XOM", "CVX", "DIS", "INTC", "CSCO",
]

EUROPE = [
    "ASML.AS",   # ASML
    "SAP.DE",    # SAP
    "MC.PA",     # LVMH
    "OR.PA",     # L'Oréal
    "TTE.PA",    # TotalEnergies
    "AIR.PA",    # Airbus
    "SAN.PA",    # Sanofi
    "SIE.DE",    # Siemens
    "ALV.DE",    # Allianz
    "BAS.DE",    # BASF
    "NOVN.SW",   # Novartis
    "NESN.SW",   # Nestlé
    "ROG.SW",    # Roche
    "AZN.L",     # AstraZeneca
    "SHEL.L",    # Shell
    "HSBA.L",    # HSBC
    "ULVR.L",    # Unilever
    "IBE.MC",    # Iberdrola
]

NORDIC = [
    "NOVO-B.CO",   # Novo Nordisk
    "MAERSK-B.CO", # Maersk
    "VWS.CO",      # Vestas
    "ORSTED.CO",   # Ørsted
    "DSV.CO",      # DSV
    "GMAB.CO",     # Genmab
    "COLO-B.CO",   # Coloplast
    "ERIC-B.ST",   # Ericsson
    "VOLV-B.ST",   # Volvo
    "ATCO-A.ST",   # Atlas Copco
    "INVE-B.ST",   # Investor AB
    "HM-B.ST",     # H&M
    "SAND.ST",     # Sandvik
    "EQNR.OL",     # Equinor
    "DNB.OL",      # DNB
    "TEL.OL",      # Telenor
    "NOKIA.HE",    # Nokia
    "SAMPO.HE",    # Sampo
]

EMERGING_MARKETS = [
    "TSM",    # Taiwan Semiconductor (ADR)
    "BABA",   # Alibaba (ADR)
    "PDD",    # PDD Holdings (ADR)
    "JD",     # JD.com (ADR)
    "TCEHY",  # Tencent (ADR)
    "INFY",   # Infosys (ADR)
    "IBN",    # ICICI Bank (ADR)
    "HDB",    # HDFC Bank (ADR)
    "MELI",   # MercadoLibre
    "VALE",   # Vale
    "ITUB",   # Itaú Unibanco (ADR)
    "PBR",    # Petrobras (ADR)
    "AMX",    # América Móvil (ADR)
]

# Benchmark: Jacobi har allerede iShares MSCI ACWI UCITS ETF (IE00B6R52259) i
# ratepensionen. Det US-listede søster-produkt "ACWI" (iShares MSCI ACWI ETF)
# bruges her som benchmark, da det er tilgængeligt via yfinance og
# repræsenterer samme globale aktieeksponering.
BENCHMARK = "ACWI"

def full_universe():
    """Return the full ticker list (all regions), de-duplicated, order preserved."""
    seen = set()
    out = []
    for group in (US, EUROPE, NORDIC, EMERGING_MARKETS):
        for t in group:
            if t not in seen:
                seen.add(t)
                out.append(t)
    return out

def region_of(ticker):
    if ticker in US:
        return "US"
    if ticker in EUROPE:
        return "Europa"
    if ticker in NORDIC:
        return "Norden"
    if ticker in EMERGING_MARKETS:
        return "Emerging Markets"
    return "Ukendt"


# ============================================================================
# Fra: swing_agent/config.py
# ============================================================================
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


# ============================================================================
# Fra: swing_agent/data_fetch.py
# ============================================================================
"""
Henter og cacher historiske daglige kurser via yfinance.

Kør dette script direkte for at fylde cachen op, eller importér
`load_prices()` fra andre scripts.

VIGTIGT: Dette script kræver almindelig internetadgang til Yahoo Finance.
Det er IKKE testet i det sandbox-miljø hvor agenten oprindeligt blev bygget
(den havde kun adgang til pypi/npm/github) og er heller IKKE testet på iOS —
kør `python data_fetch.py --check` først for at bekræfte at det virker i dit
miljø, før du kaster hele universet på det. Se README.md, afsnittet
"Kør fra iPhone", hvis yfinance ikke vil installere/køre.

Cache-format: almindelig CSV (ikke parquet) — undgår pyarrow, som er
tung/besværlig at få installeret i mange mobile Python-apps.
"""

import os
import sys
import time
import pandas as pd




CACHE_DIR = os.path.join(".", "cache")


def _cache_path(ticker: str) -> str:
    safe = ticker.replace("/", "_")
    return os.path.join(CACHE_DIR, f"{safe}.csv")


def fetch_ticker(ticker: str, start: str, end: str, force: bool = False) -> pd.DataFrame:
    """Fetch (or load cached) daily OHLCV for one ticker. Returns adjusted close
    as 'AdjClose' plus raw OHLCV. Empty DataFrame on failure."""
    try:
        import yfinance as yf
    except ImportError as e:
        raise ImportError(
            "yfinance kunne ikke importeres. Kør 'pip install -r requirements.txt' "
            "(pinner yfinance==0.2.55, som kun kræver rene Python-pakker). Hvis "
            "'pip install' selv fejlede på en anden pakke, så virker den formentlig "
            "ikke på din platform uden kompiler — se README.md 'Kør fra iPhone'."
        ) from e

    path = _cache_path(ticker)
    if not force and os.path.exists(path):
        try:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if not df.empty:
                return df
        except Exception:
            pass

    for attempt in range(3):
        try:
            df = yf.download(
                ticker, start=start, end=end, auto_adjust=False,
                progress=False, threads=False,
            )
            if df is None or df.empty:
                raise ValueError("tom respons")
            # yfinance can return MultiIndex columns for single-ticker calls
            # depending on version; normalise to flat columns.
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.get_level_values(0)
            df = df.rename(columns={"Adj Close": "AdjClose"})
            df = df[["Open", "High", "Low", "Close", "AdjClose", "Volume"]].dropna(
                subset=["AdjClose"]
            )
            os.makedirs(CACHE_DIR, exist_ok=True)
            df.to_csv(path)
            return df
        except Exception as e:
            print(f"  [{ticker}] forsøg {attempt+1}/3 fejlede: {e}", file=sys.stderr)
            time.sleep(1.5 * (attempt + 1))

    print(f"  [{ticker}] KUNNE IKKE HENTES — udelades af universet.", file=sys.stderr)
    return pd.DataFrame()


def fetch_all(cfg: Config, force: bool = False) -> dict:
    """Fetch benchmark + full universe. Returns {ticker: DataFrame}."""
    end = cfg.end_date or pd.Timestamp.today().strftime("%Y-%m-%d")
    tickers = [BENCHMARK] + full_universe()
    data = {}
    print(f"Henter kursdata for {len(tickers)} tickere ({cfg.start_date} -> {end})...")
    for i, t in enumerate(tickers, 1):
        print(f"[{i}/{len(tickers)}] {t}")
        df = fetch_ticker(t, cfg.start_date, end, force=force)
        if not df.empty and len(df) >= cfg.min_history_days:
            data[t] = df
        elif not df.empty:
            print(f"  [{t}] for kort historik ({len(df)} dage) — udelades.")
    missing = set(tickers) - set(data.keys())
    if missing:
        print(f"\nAdvarsel: {len(missing)} tickere kunne ikke hentes/opfylder ikke "
              f"minimumshistorik: {sorted(missing)}")
    print(f"\nFærdig. {len(data)} tickere klar (inkl. benchmark).")
    return data


def load_prices(cfg: Config, force: bool = False) -> pd.DataFrame:
    """Return a single wide DataFrame of AdjClose, columns = tickers."""
    data = fetch_all(cfg, force=force)
    if not data:
        raise RuntimeError(
            "Ingen data hentet. Tjek internetforbindelse og at yfinance er "
            "installeret (pip install -r requirements.txt)."
        )
    series = {t: df["AdjClose"] for t, df in data.items()}
    wide = pd.DataFrame(series).sort_index()
    # Forward-fill korte huller (helligdage der ikke matcher på tværs af
    # børser), men dropp rækker hvor benchmark mangler.
    wide = wide.ffill(limit=5)
    return wide


def check_environment():
    """Hurtig diagnosticering: kan vi importere yfinance og hente ÉN ticker?
    Kør: python data_fetch.py --check
    Nyttigt til at fejlsøge på telefonen før hele universet hentes."""
    print("1) Importerer yfinance...")
    try:
        import yfinance as yf
        print("   OK.")
    except Exception as e:
        print(f"   FEJL: {e}")
        print("   -> yfinance er ikke installeret korrekt. Se README.md 'Kør fra iPhone'.")
        return False

    print("2) Henter 5 dages data for AAPL (test)...")
    try:
        df = yf.download("AAPL", period="5d", progress=False, threads=False)
        if df is None or df.empty:
            print("   FEJL: tom respons — sandsynligvis blokeret/rate-limited af Yahoo Finance.")
            return False
        print(f"   OK — modtog {len(df)} rækker.")
    except Exception as e:
        print(f"   FEJL: {e}")
        print("   -> Sandsynligvis netværksproblem eller anti-bot-blokering. Se README.md.")
        return False

    print("\nMiljøet ser klar ud. Kør 'python run_backtest.py' for den fulde kørsel.")
    return True



# ============================================================================
# Fra: swing_agent/report.py
# ============================================================================
"""
Genererer en selvstændig HTML-rapport med grafer og nøgletal.

Ingen matplotlib — graferne er håndbyggede inline SVG'er i ren Python
(ingen ekstra afhængighed, kører overalt, også i letvægts iOS/Android
Python-apps). Farvevalg følger Anthropics dataviz-retningslinjer
(kategorisk rækkefølge, statusfarver forbeholdt op/ned, lav-kontrast
gitterlinjer, ingen dobbelt y-akse).
"""

import os
import html as _html

import pandas as pd




# --- Farvepalet (fra dataviz-skillet, lys tilstand) ---
SURFACE = "#fcfcfb"
PAGE = "#f9f9f7"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE = "#c3c2b7"
SERIES_BLUE = "#2a78d6"     # kategorisk slot 1 — strategi
SERIES_ORANGE = "#eb6834"   # kategorisk slot 2 — benchmark
SERIES_RED = "#e34948"      # kategorisk slot 8 — drawdown
STATUS_GOOD = "#0ca30c"
STATUS_CRITICAL = "#d03b3b"


# ---------------------------------------------------------------------
# SVG-hjælpefunktioner (ingen matplotlib, ingen eksterne afhængigheder)
# ---------------------------------------------------------------------

def _scale(v, vmin, vmax, out_min, out_max):
    if vmax == vmin:
        return (out_min + out_max) / 2
    return out_min + (v - vmin) / (vmax - vmin) * (out_max - out_min)


def _nice_ticks(vmin, vmax, n=5):
    if vmin == vmax:
        vmin -= 1
        vmax += 1
    span = vmax - vmin
    step_raw = span / max(n - 1, 1)
    mag = 10 ** (len(str(int(step_raw))) - 1) if step_raw >= 1 else 1
    for mult in (1, 2, 2.5, 5, 10):
        step = mag * mult
        if step >= step_raw:
            break
    start = (vmin // step) * step
    ticks = []
    t = start
    while t <= vmax + step:
        ticks.append(t)
        t += step
    return ticks


def line_chart_svg(series_dict: dict, colors: dict, title: str,
                    width=720, height=280, y_fmt=lambda v: f"{v:.0f}") -> str:
    """series_dict: {name: pd.Series} sharing a common (already-aligned) index."""
    pad_l, pad_r, pad_t, pad_b = 52, 16, 40, 30
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b

    idx = next(iter(series_dict.values())).index
    n = len(idx)
    all_vals = pd.concat(series_dict.values())
    vmin, vmax = float(all_vals.min()), float(all_vals.max())
    vspan = (vmax - vmin) or 1
    vmin -= vspan * 0.06
    vmax += vspan * 0.06

    def x_at(i):
        return pad_l + (i / max(n - 1, 1)) * plot_w

    def y_at(v):
        return pad_t + plot_h - _scale(v, vmin, vmax, 0, plot_h)

    ticks = _nice_ticks(vmin, vmax, 5)
    grid_svg = []
    for t in ticks:
        if t < vmin or t > vmax:
            continue
        y = y_at(t)
        grid_svg.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" '
                         f'stroke="{GRIDLINE}" stroke-width="1"/>')
        grid_svg.append(f'<text x="{pad_l-8}" y="{y+3:.1f}" text-anchor="end" '
                         f'font-size="10" fill="{INK_MUTED}">{y_fmt(t)}</text>')

    # x-axis year labels (sparse)
    n_labels = min(6, n)
    x_labels = []
    if n > 1:
        step = max(n // n_labels, 1)
        for i in range(0, n, step):
            x_labels.append(
                f'<text x="{x_at(i):.1f}" y="{height-pad_b+18}" text-anchor="middle" '
                f'font-size="10" fill="{INK_MUTED}">{idx[i].strftime("%Y")}</text>'
            )

    lines_svg = []
    legend_svg = []
    for j, (name, s) in enumerate(series_dict.items()):
        pts = " ".join(f"{x_at(i):.1f},{y_at(v):.1f}" for i, v in enumerate(s.values))
        color = colors[name]
        lines_svg.append(f'<polyline points="{pts}" fill="none" stroke="{color}" '
                          f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>')
        lx = pad_l + j * 150
        legend_svg.append(f'<line x1="{lx}" y1="14" x2="{lx+16}" y2="14" stroke="{color}" stroke-width="3"/>')
        legend_svg.append(f'<text x="{lx+22}" y="18" font-size="11" fill="{INK_SECONDARY}">{_html.escape(name)}</text>')

    baseline_y = pad_t + plot_h
    svg = f"""<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{_html.escape(title)}">
      <rect x="0" y="0" width="{width}" height="{height}" fill="{SURFACE}"/>
      <text x="{pad_l}" y="20" font-size="13" fill="{INK_PRIMARY}" font-weight="600">{_html.escape(title)}</text>
      {''.join(legend_svg)}
      {''.join(grid_svg)}
      <line x1="{pad_l}" y1="{baseline_y}" x2="{width-pad_r}" y2="{baseline_y}" stroke="{BASELINE}" stroke-width="1"/>
      {''.join(lines_svg)}
      {''.join(x_labels)}
    </svg>"""
    return svg


def area_chart_svg(series: pd.Series, color: str, title: str,
                    width=720, height=200, y_fmt=lambda v: f"{v:.0f}%") -> str:
    pad_l, pad_r, pad_t, pad_b = 52, 16, 40, 26
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    n = len(series)
    vmin, vmax = float(series.min()), 0.0
    if vmin == vmax:
        vmin = -1.0
    vspan = (vmax - vmin) or 1

    def x_at(i):
        return pad_l + (i / max(n - 1, 1)) * plot_w

    def y_at(v):
        return pad_t + plot_h - _scale(v, vmin, vmax, 0, plot_h)

    ticks = _nice_ticks(vmin, vmax, 4)
    grid_svg = []
    for t in ticks:
        if t < vmin or t > vmax:
            continue
        y = y_at(t)
        grid_svg.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" '
                         f'stroke="{GRIDLINE}" stroke-width="1"/>')
        grid_svg.append(f'<text x="{pad_l-8}" y="{y+3:.1f}" text-anchor="end" '
                         f'font-size="10" fill="{INK_MUTED}">{y_fmt(t)}</text>')

    pts = [(x_at(i), y_at(v)) for i, v in enumerate(series.values)]
    poly_pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    zero_y = y_at(0)
    area_pts = f"{pad_l:.1f},{zero_y:.1f} " + poly_pts + f" {x_at(n-1):.1f},{zero_y:.1f}"

    n_labels = min(6, n)
    x_labels = []
    if n > 1:
        step = max(n // n_labels, 1)
        for i in range(0, n, step):
            x_labels.append(
                f'<text x="{x_at(i):.1f}" y="{height-pad_b+18}" text-anchor="middle" '
                f'font-size="10" fill="{INK_MUTED}">{series.index[i].strftime("%Y")}</text>'
            )

    svg = f"""<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{_html.escape(title)}">
      <rect x="0" y="0" width="{width}" height="{height}" fill="{SURFACE}"/>
      <text x="{pad_l}" y="20" font-size="13" fill="{INK_PRIMARY}" font-weight="600">{_html.escape(title)}</text>
      {''.join(grid_svg)}
      <polygon points="{area_pts}" fill="{color}" opacity="0.22" stroke="none"/>
      <polyline points="{poly_pts}" fill="none" stroke="{color}" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/>
      <line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width-pad_r}" y2="{zero_y:.1f}" stroke="{BASELINE}" stroke-width="1"/>
      {''.join(x_labels)}
    </svg>"""
    return svg


def bar_chart_svg(q_df: pd.DataFrame, title: str, width=720, height=260) -> str:
    pad_l, pad_r, pad_t, pad_b = 52, 16, 40, 54
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    n = len(q_df)
    vals = (q_df["return"] * 100)
    vmin, vmax = float(vals.min()), float(vals.max())
    vmin = min(vmin, 0)
    vmax = max(vmax, 0)
    vspan = (vmax - vmin) or 1
    vmin -= vspan * 0.12
    vmax += vspan * 0.12

    def y_at(v):
        return pad_t + plot_h - _scale(v, vmin, vmax, 0, plot_h)

    zero_y = y_at(0)
    bw = plot_w / max(n, 1) * 0.6
    gap = plot_w / max(n, 1)

    ticks = _nice_ticks(vmin, vmax, 4)
    grid_svg = []
    for t in ticks:
        if t < vmin or t > vmax:
            continue
        y = y_at(t)
        grid_svg.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width-pad_r}" y2="{y:.1f}" '
                         f'stroke="{GRIDLINE}" stroke-width="1"/>')
        grid_svg.append(f'<text x="{pad_l-8}" y="{y+3:.1f}" text-anchor="end" '
                         f'font-size="10" fill="{INK_MUTED}">{t:.0f}%</text>')

    bars_svg = []
    for i, (dt, row) in enumerate(q_df.iterrows()):
        v = row["return"] * 100
        color = STATUS_GOOD if v >= 0 else STATUS_CRITICAL
        cx = pad_l + gap * i + gap / 2
        y_top = min(zero_y, y_at(v))
        h = abs(y_at(v) - zero_y)
        bars_svg.append(f'<rect x="{cx-bw/2:.1f}" y="{y_top:.1f}" width="{bw:.1f}" height="{max(h,1):.1f}" '
                         f'fill="{color}" rx="2"/>')
        label_y = y_at(v) - 5 if v >= 0 else y_at(v) + 12
        bars_svg.append(f'<text x="{cx:.1f}" y="{label_y:.1f}" text-anchor="middle" font-size="8" '
                         f'fill="{INK_SECONDARY}">{v:+.1f}%</text>')
        bars_svg.append(f'<text x="{cx:.1f}" y="{height-pad_b+16}" text-anchor="middle" font-size="8" '
                         f'fill="{INK_MUTED}" transform="rotate(-45 {cx:.1f} {height-pad_b+16})">'
                         f'{dt.year} K{dt.quarter}</text>')

    svg = f"""<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="{_html.escape(title)}">
      <rect x="0" y="0" width="{width}" height="{height}" fill="{SURFACE}"/>
      <text x="{pad_l}" y="20" font-size="13" fill="{INK_PRIMARY}" font-weight="600">{_html.escape(title)}</text>
      {''.join(grid_svg)}
      <line x1="{pad_l}" y1="{zero_y:.1f}" x2="{width-pad_r}" y2="{zero_y:.1f}" stroke="{BASELINE}" stroke-width="1"/>
      {''.join(bars_svg)}
    </svg>"""
    return svg


def _weekly(series: pd.Series) -> pd.Series:
    return series.resample("W").last().dropna()


def build_equity_svg(equity: pd.Series, benchmark: pd.Series) -> str:
    idx_equity = _weekly(equity / equity.iloc[0] * 100)
    idx_bench = _weekly(benchmark / benchmark.iloc[0] * 100)
    common = idx_equity.index.union(idx_bench.index)
    idx_equity = idx_equity.reindex(common).ffill().bfill()
    idx_bench = idx_bench.reindex(common).ffill().bfill()
    return line_chart_svg(
        {"Swing-agent": idx_equity, "Benchmark (ACWI)": idx_bench},
        {"Swing-agent": SERIES_BLUE, "Benchmark (ACWI)": SERIES_ORANGE},
        "Porteføljeværdi vs. benchmark (indekseret, start = 100)",
        y_fmt=lambda v: f"{v:.0f}",
    )


def build_drawdown_svg(equity: pd.Series) -> str:
    running_max = equity.cummax()
    dd = _weekly((equity / running_max - 1.0) * 100)
    return area_chart_svg(dd, SERIES_RED, "Drawdown fra seneste top")


def build_quarterly_svg(q_df: pd.DataFrame) -> str:
    return bar_chart_svg(q_df, "Afkast pr. 3-måneders cyklus")


# ---------------------------------------------------------------------
# HTML-rapport
# ---------------------------------------------------------------------

def _fmt_pct(x):
    return "–" if pd.isna(x) else f"{x*100:+.1f}%"


def _fmt_num(x, digits=2):
    return "–" if pd.isna(x) else f"{x:.{digits}f}"


def build_html(cfg: Config, summ: dict, trade_log: list, universe_size: int,
                missing_tickers: list, current_candidates: pd.DataFrame,
                equity_svg: str, dd_svg: str, q_svg: str) -> str:

    stat_tiles = [
        ("Samlet afkast", _fmt_pct(summ["total_return"]), _fmt_pct(summ["bench_total_return"])),
        ("CAGR (årligt)", _fmt_pct(summ["cagr"]), _fmt_pct(summ["bench_cagr"])),
        ("Max drawdown", _fmt_pct(summ["max_drawdown"]), _fmt_pct(summ["bench_max_drawdown"])),
        ("Sharpe-ratio", _fmt_num(summ["sharpe"]), _fmt_num(summ["bench_sharpe"])),
    ]
    tiles_html = "".join(f"""
      <div class="tile">
        <div class="tile-label">{label}</div>
        <div class="tile-value">{val}</div>
        <div class="tile-bench">Benchmark: {bench}</div>
      </div>""" for label, val, bench in stat_tiles)

    trade_rows = "".join(f"""
      <tr>
        <td>{_html.escape(t['ticker'])}</td>
        <td>{_html.escape(region_of(t['ticker']))}</td>
        <td>{t['entry_date'].strftime('%Y-%m-%d')}</td>
        <td>{t['exit_date'].strftime('%Y-%m-%d')}</td>
        <td>{t['holding_days']}</td>
        <td class="{'pos' if t['return_pct']>=0 else 'neg'}">{_fmt_pct(t['return_pct'])}</td>
        <td>{'Stop-loss' if t['reason']=='stop_loss' else 'Rotation'}</td>
      </tr>""" for t in sorted(trade_log, key=lambda x: x["exit_date"], reverse=True)[:30])

    cand_rows = "".join(f"""
      <tr>
        <td>{_html.escape(tk)}</td>
        <td>{_html.escape(region_of(tk))}</td>
        <td>{_fmt_pct(row['momentum'])}</td>
        <td>{row['last_price']:.2f}</td>
      </tr>""" for tk, row in current_candidates.head(10).iterrows())

    missing_html = ""
    if missing_tickers:
        missing_html = f"""
        <p class="caveat">{len(missing_tickers)} tickere kunne ikke hentes og indgår ikke i
        denne kørsel: {_html.escape(", ".join(missing_tickers))}</p>"""

    n_trades = summ["trade_n_trades"]

    return f"""<!doctype html>
<html lang="da">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Swing trading-agent — backtest-rapport</title>
<style>
  body {{ background:{PAGE}; color:{INK_PRIMARY}; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         max-width: 800px; margin: 0 auto; padding: 24px 16px 80px; line-height:1.5; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.05rem; margin-top: 36px; border-bottom: 1px solid {GRIDLINE}; padding-bottom: 6px; }}
  .subtitle {{ color:{INK_SECONDARY}; margin-top:0; font-size:0.9rem; }}
  .tiles {{ display:flex; gap:10px; flex-wrap:wrap; margin: 18px 0; }}
  .tile {{ background:{SURFACE}; border:1px solid {GRIDLINE}; border-radius:10px; padding:12px 16px; flex:1; min-width:140px; }}
  .tile-label {{ font-size:0.75rem; color:{INK_MUTED}; }}
  .tile-value {{ font-size:1.35rem; font-weight:600; margin-top:2px; }}
  .tile-bench {{ font-size:0.72rem; color:{INK_SECONDARY}; margin-top:4px; }}
  .chart {{ background:{SURFACE}; border:1px solid {GRIDLINE}; border-radius:10px; padding:8px; margin:14px 0; overflow-x:auto; }}
  .chart svg {{ width:100%; height:auto; display:block; min-width:480px; }}
  table {{ width:100%; border-collapse: collapse; font-size:0.82rem; margin-top:10px; display:block; overflow-x:auto; white-space:nowrap; }}
  th, td {{ text-align:left; padding:6px 8px; border-bottom:1px solid {GRIDLINE}; }}
  th {{ color:{INK_MUTED}; font-weight:600; font-size:0.72rem; text-transform:uppercase; }}
  td.pos {{ color:{STATUS_GOOD}; }}
  td.neg {{ color:{STATUS_CRITICAL}; }}
  .caveat {{ background:#fff8e6; border:1px solid #f0d98c; border-radius:8px; padding:10px 14px; font-size:0.82rem; color:{INK_SECONDARY}; }}
  .assumptions li {{ margin-bottom:6px; }}
  footer {{ margin-top:44px; color:{INK_MUTED}; font-size:0.75rem; }}
</style>
</head>
<body>
  <h1>Swing trading-agent — backtest-rapport</h1>
  <p class="subtitle">Trend/momentum-strategi · 3 måneders rebalanceringscyklus · maks {cfg.max_positions} positioner ·
  simuleret startkapital {cfg.starting_capital:,.0f} (paper trading, ingen ægte handler) ·
  periode {summ['start_date'].strftime('%Y-%m-%d')} – {summ['end_date'].strftime('%Y-%m-%d')}</p>

  <div class="tiles">{tiles_html}</div>

  <h2>Porteføljeudvikling</h2>
  <div class="chart">{equity_svg}</div>
  <div class="chart">{dd_svg}</div>
  <div class="chart">{q_svg}</div>

  <h2>Handelsstatistik</h2>
  <table>
    <tr><th>Antal handler</th><td>{n_trades}</td>
        <th>Andel vindere</th><td>{_fmt_pct(summ['trade_win_rate'])}</td></tr>
    <tr><th>Gns. afkast/handel</th><td>{_fmt_pct(summ['trade_avg_return_pct'])}</td>
        <th>Gns. holdeperiode</th><td>{_fmt_num(summ['trade_avg_holding_days'],0)} dage</td></tr>
    <tr><th>Bedste handel</th><td>{_fmt_pct(summ['trade_best_trade_pct'])}</td>
        <th>Værste handel</th><td>{_fmt_pct(summ['trade_worst_trade_pct'])}</td></tr>
  </table>

  <h2>Aktuelle topkandidater (seneste dato i datasættet)</h2>
  <table>
    <tr><th>Ticker</th><th>Region</th><th>Momentum (6 mdr., skip 1 mdr.)</th><th>Seneste kurs</th></tr>
    {cand_rows if cand_rows else '<tr><td colspan="4">Ingen kandidater bestod trendfilteret på seneste dato.</td></tr>'}
  </table>

  <h2>Handelslog (seneste 30)</h2>
  <table>
    <tr><th>Ticker</th><th>Region</th><th>Indgang</th><th>Udgang</th><th>Dage</th><th>Afkast</th><th>Årsag</th></tr>
    {trade_rows if trade_rows else '<tr><td colspan="7">Ingen lukkede handler i perioden.</td></tr>'}
  </table>

  <h2>Univers og datadækning</h2>
  <p>{universe_size} tickere forsøgt hentet (US, Europa, Norden, emerging markets) + benchmark.</p>
  {missing_html}

  <h2>Antagelser og begrænsninger</h2>
  <ul class="assumptions">
    <li>Simulerede/"paper" tal — INGEN ægte handler er foretaget, og resultatet er ikke en garanti for fremtidig performance.</li>
    <li>Valuta: afkast regnes i hver akties lokale valuta og vægtes sammen uden FX-konvertering. Nordnets vekslingsgebyr ved handel i fremmed valuta (typisk ~0,25–0,5%) er IKKE medregnet.</li>
    <li>Omkostninger er approksimeret ({cfg.cost_pct*100:.2f}% pr. handel, min. {cfg.min_fee_local:.0f} pr. handel) — bekræft Nordnets faktiske kurtagesatser før eventuel reel handel.</li>
    <li>Handler eksekveres til dagens slutkurs på beslutningsdagen (ingen slippage/næste-dags-udførelse modelleret).</li>
    <li>Sharpe-ratio antager 0% risikofri rente.</li>
    <li>Overlevelsesbias: universet er en fast liste af i dag likvide aktier — afnoterede/konkursramte selskaber fra perioden er ikke inkluderet.</li>
    <li>Dette er backtest på historiske data — INGEN garanti for at strategien virker fremadrettet.</li>
  </ul>

  <footer>Genereret af swing_agent-projektet. Kun til eget analysebrug — ikke finansiel rådgivning.</footer>
</body>
</html>"""


def generate_report(cfg: Config, prices_wide: pd.DataFrame, result: dict,
                     missing_tickers: list, universe_size: int, out_path: str):




    equity = result["equity_curve"]
    bench = result["benchmark_curve"]
    summ = summary(equity, bench, result["trade_log"])
    q_df = quarterly_returns(equity)

    equity_svg = build_equity_svg(equity, bench)
    dd_svg = build_drawdown_svg(equity)
    q_svg = build_quarterly_svg(q_df)

    universe_cols = [c for c in prices_wide.columns if c != BENCHMARK]
    current_candidates = rank_candidates(prices_wide[universe_cols], prices_wide.index[-1], cfg)

    html_out = build_html(cfg, summ, result["trade_log"], universe_size, missing_tickers,
                           current_candidates, equity_svg, dd_svg, q_svg)

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return out_path, summ


# ============================================================================
# Fra: swing_agent/technicals.py
# ============================================================================
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


# ============================================================================
# Fra: swing_agent/sentiment.py
# ============================================================================
"""
Analytiker- og sentiment-data til kandidat-screeneren.

Tre kilder, hver med faldende pålidelighed — alle er "best effort" og fejler
ALDRIG hårdt: returnerer altid et dict med "ok": True/False + en forklaring,
så én kildes nedetid ikke vælter hele screener-kørslen.

1. Analytiker-data (yfinance/Yahoo Finance): kursmål, antal analytikere,
   købs-/salgsanbefaling, seneste op-/nedgraderinger. Relativt pålideligt,
   ingen ekstra afhængighed (bruger allerede-installeret yfinance).

2. StockTwits (offentligt, uautoriseret API — api.stocktwits.com): "bullish/
   bearish"-tags på seneste opslag + watchlist-antal som popularitetsmål.
   Dækker stort set kun US-tickere/ADR'er (ikke europæiske/nordiske
   hjemmemarkeds-tickere). Ingen garanti for oppetid/rate-limits — det er en
   uofficiel, uautentificeret endpoint, IKKE testet fra dette sandbox-miljø
   (ingen internetadgang her), så behandl fejl herfra som forventelige.

3. Reddit (praw): tæller opslag der nævner tickeren/selskabet i udvalgte
   subreddits seneste uge. KRÆVER at du selv opretter en gratis Reddit "app"
   (script-type) på reddit.com/prefs/apps og gemmer client_id/secret som
   Colab-secrets (nøgleikonet i venstre sidebjælke) under navnene
   REDDIT_CLIENT_ID og REDDIT_CLIENT_SECRET — se README.md. Uden dette
   udelades Reddit-delen automatisk (ingen fejl, bare et "ikke konfigureret").
"""

import os
import time

import pandas as pd


# ---------------------------------------------------------------------
# 1) Analytiker-data (yfinance)
# ---------------------------------------------------------------------

def get_analyst_data(ticker: str) -> dict:
    try:
        import yfinance as yf
    except ImportError:
        return {"ok": False, "reason": "yfinance ikke installeret"}

    try:
        t = yf.Ticker(ticker)
        info = {}
        try:
            info = t.info or {}
        except Exception:
            pass

        target_mean = info.get("targetMeanPrice")
        target_high = info.get("targetHighPrice")
        target_low = info.get("targetLowPrice")
        num_analysts = info.get("numberOfAnalystOpinions")
        rec_mean = info.get("recommendationMean")  # 1=Strong Buy .. 5=Sell
        rec_key = info.get("recommendationKey")
        current_price = info.get("currentPrice") or info.get("regularMarketPrice")
        short_name = info.get("shortName") or info.get("longName")

        upgrades_90d, downgrades_90d = None, None
        try:
            ud = t.upgrades_downgrades
            if ud is not None and not ud.empty:
                idx = ud.index if not isinstance(ud.index, pd.RangeIndex) else pd.to_datetime(ud.get("GradeDate", ud.index))
                ud = ud.copy()
                ud.index = pd.to_datetime(idx, errors="coerce", utc=True).tz_localize(None) if hasattr(idx, "tz") else pd.to_datetime(idx, errors="coerce")
                cutoff = pd.Timestamp.today() - pd.Timedelta(days=90)
                recent = ud[ud.index >= cutoff]
                action_col = "Action" if "Action" in recent.columns else None
                if action_col:
                    upgrades_90d = int((recent[action_col].str.lower() == "up").sum())
                    downgrades_90d = int((recent[action_col].str.lower() == "down").sum())
        except Exception:
            pass  # upgrades/downgrades er "nice to have" — fejl her er ikke fatalt

        analyst_score = None
        if rec_mean is not None:
            score_rec = max(0.0, min(100.0, (5.0 - float(rec_mean)) / 4.0 * 100.0))
            if upgrades_90d is not None and downgrades_90d is not None:
                momentum = upgrades_90d - downgrades_90d
                momentum_score = max(0.0, min(100.0, 50.0 + momentum * 10.0))
                analyst_score = 0.7 * score_rec + 0.3 * momentum_score
            else:
                analyst_score = score_rec

        return {
            "ok": True,
            "current_price": current_price,
            "short_name": short_name,
            "target_mean": target_mean,
            "target_high": target_high,
            "target_low": target_low,
            "num_analysts": num_analysts,
            "recommendation_mean": rec_mean,
            "recommendation_key": rec_key,
            "upgrades_90d": upgrades_90d,
            "downgrades_90d": downgrades_90d,
            "analyst_score": analyst_score,
        }
    except Exception as e:
        return {"ok": False, "reason": f"kunne ikke hente analytiker-data: {e}"}


# ---------------------------------------------------------------------
# 2) StockTwits
# ---------------------------------------------------------------------

def get_stocktwits_data(ticker: str, cfg) -> dict:
    try:
        import requests
    except ImportError:
        return {"ok": False, "reason": "requests ikke installeret"}

    symbol = ticker.split(".")[0]  # StockTwits kender kun US-stil symboler
    url = f"https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (swing_agent screener; personligt analyseværktøj)"},
            timeout=8,
        )
        if resp.status_code != 200:
            return {"ok": False, "reason": f"StockTwits svarede HTTP {resp.status_code} (rate-limit/ukendt symbol?)"}
        data = resp.json()
        watchlist_count = (data.get("symbol") or {}).get("watchlist_count")
        messages = data.get("messages") or []
        tagged = [
            m for m in messages
            if (m.get("entities") or {}).get("sentiment") is not None
        ]
        bullish = sum(
            1 for m in tagged
            if (m["entities"]["sentiment"] or {}).get("basic") == "Bullish"
        )
        sample_size = len(tagged)
        bullish_ratio = (bullish / sample_size * 100.0) if sample_size else None

        stocktwits_score = None
        if bullish_ratio is not None:
            weight = min(sample_size / 10.0, 1.0)
            stocktwits_score = weight * bullish_ratio + (1 - weight) * 50.0

        return {
            "ok": True,
            "watchlist_count": watchlist_count,
            "message_count": len(messages),
            "tagged_sample_size": sample_size,
            "bullish_ratio": bullish_ratio,
            "stocktwits_score": stocktwits_score,
        }
    except Exception as e:
        return {"ok": False, "reason": f"StockTwits-opslag fejlede: {e}"}


# ---------------------------------------------------------------------
# 3) Reddit (praw) — kræver Colab-secrets, udelades gracefully uden
# ---------------------------------------------------------------------

def _get_secret(name: str):
    try:
        from google.colab import userdata  # kun tilgængelig i Colab
        val = userdata.get(name)
        if val:
            return val
    except Exception:
        pass
    return os.environ.get(name)


def get_reddit_data(ticker: str, cfg, company_name: str = None) -> dict:
    try:
        import praw
    except ImportError:
        return {"ok": False, "reason": "praw ikke installeret — Reddit-sentiment udelades (pip install praw)"}

    client_id = _get_secret("REDDIT_CLIENT_ID")
    client_secret = _get_secret("REDDIT_CLIENT_SECRET")
    if not client_id or not client_secret:
        return {
            "ok": False,
            "reason": "Reddit API-nøgler ikke fundet (Colab-secrets REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET) — se README.md",
        }

    try:
        reddit = praw.Reddit(
            client_id=client_id,
            client_secret=client_secret,
            user_agent="swing_agent_screener/1.0 (personligt analyseværktøj)",
        )
        reddit.read_only = True

        base_symbol = ticker.split(".")[0]
        search_terms = {base_symbol}
        if company_name:
            search_terms.add(company_name)

        mentions = []
        for sub_name in cfg.reddit_subreddits:
            try:
                subreddit = reddit.subreddit(sub_name)
                for term in search_terms:
                    for post in subreddit.search(term, time_filter=cfg.reddit_time_filter,
                                                  limit=cfg.reddit_search_limit):
                        mentions.append({
                            "subreddit": sub_name,
                            "title": post.title,
                            "score": post.score,
                            "num_comments": post.num_comments,
                        })
            except Exception:
                continue  # én subreddit der fejler (fx nedlagt/privat) må ikke vælte resten

        # dedupliker (samme post kan matche flere søgetermer)
        seen = set()
        unique = []
        for m in mentions:
            key = (m["subreddit"], m["title"])
            if key not in seen:
                seen.add(key)
                unique.append(m)

        avg_score = (sum(m["score"] for m in unique) / len(unique)) if unique else 0.0
        return {
            "ok": True,
            "mention_count": len(unique),
            "avg_score": avg_score,
            "top_mentions": sorted(unique, key=lambda m: m["score"], reverse=True)[:3],
        }
    except Exception as e:
        return {"ok": False, "reason": f"Reddit-opslag fejlede: {e}"}


# ============================================================================
# Fra: swing_agent/screener.py
# ============================================================================
"""
Kandidat-screener: rangerer aktier efter "hvor moden er den til køb lige nu",
kombineret af teknisk analyse (TA), analytiker-konsensus og popularitet/
sentiment (analytikere + StockTwits + Reddit).

Dette er IKKE det samme som backtest-strategien i strategy.py/backtest.py.
Screeneren er et forward-looking øjebliksbillede ("hvad ser interessant ud
lige nu") og bruger data der ikke findes historisk på en måde der kan
no-lookahead-backtestes (analytikerkonsensus og social sentiment findes
kun for "i dag", ikke pænt tidsstemplet bagud i tid). Den erstatter ikke
backtesten — brug den som et supplerende research-værktøj.

**Vigtigt: dette er IKKE finansiel rådgivning.** Scoren er en gennemsigtig,
regelbaseret sammenvejning af offentligt tilgængelige signaler — ikke en
forudsigelse. Lav altid egen research før du handler, og husk at analytiker-
kursmål og social sentiment jævnligt tager fejl.

Fremgangsmåde (holder antal eksterne API-kald nede):
1. Hent kursdata for hele universet (samme cache som backtesten).
2. Beregn TA-signaler for alle tickere (gratis — ingen ekstra netværkskald,
   bruger allerede hentede kursdata) og byg en kort shortlist af de bedst
   rangerede TA-kandidater.
3. Kun for shortlisten: hent analytiker-data (yfinance), StockTwits og
   Reddit (best-effort, kan mangle for nogle/alle tickere).
4. Kombinér til en komposit-score og rangér.
"""

import sys
import time

import pandas as pd








def _compute_upside(ta: dict, analyst: dict):
    """Returnér (upside_pct eller None, kilde-tekst)."""
    last_price = ta["last_price"]
    if analyst.get("ok") and analyst.get("target_mean") and last_price:
        upside = (analyst["target_mean"] / last_price - 1.0) * 100.0
        n = analyst.get("num_analysts")
        kilde = f"Analytiker-konsensus ({n} analytikere)" if n else "Analytiker-konsensus"
        return upside, kilde
    high_252 = ta.get("high_252w")
    if high_252 and last_price and last_price < high_252:
        upside = (high_252 / last_price - 1.0) * 100.0
        return upside, "Teknisk estimat (afstand til 52-ugers højeste) — intet analytikerdækning"
    return None, "Intet analytikerdækning og ingen klar teknisk målkurs (aktien er allerede ved/over 52-ugers højeste)"


def _popularity_score(analyst: dict, stocktwits: dict, reddit_buzz_pct):
    components, weights = [], []
    if analyst.get("ok") and analyst.get("analyst_score") is not None:
        components.append(analyst["analyst_score"]); weights.append(0.5)
    if stocktwits.get("ok") and stocktwits.get("stocktwits_score") is not None:
        components.append(stocktwits["stocktwits_score"]); weights.append(0.3)
    if reddit_buzz_pct is not None:
        components.append(reddit_buzz_pct); weights.append(0.2)
    if not components:
        return None
    total_w = sum(weights)
    return sum(c * w for c, w in zip(components, weights)) / total_w


def run_screener(cfg: Config, progress=print) -> list:
    """Kør hele screener-pipelinen. Returnerer en liste af dicts, én pr.
    kandidat, sorteret efter komposit-score (højest først)."""

    progress("Henter kursdata (samme cache som backtesten)...")
    data = fetch_all(cfg)
    data.pop(BENCHMARK, None)

    progress(f"\nBeregner tekniske signaler for {len(data)} tickere...")
    ta_results = {}
    for ticker, df in data.items():
        res = compute_technical_signals(df, cfg)
        if res.get("ok"):
            ta_results[ticker] = res

    trend_ok_count = sum(1 for r in ta_results.values() if r["trend_ok"])
    progress(f"{len(ta_results)} tickere havde nok historik, {trend_ok_count} bestod trendfilteret.")

    shortlist = sorted(
        (t for t, r in ta_results.items() if r["trend_ok"]),
        key=lambda t: ta_results[t]["ta_score"],
        reverse=True,
    )[: cfg.screener_shortlist_size]

    if not shortlist:
        progress("Ingen kandidater bestod trendfilteret — ingen aktier at rangere lige nu.")
        return []

    progress(f"\nShortlist ({len(shortlist)} tickere) — henter analytiker-/sentiment-data pr. aktie...")
    candidates = []
    reddit_mentions = {}
    for i, ticker in enumerate(shortlist, 1):
        progress(f"[{i}/{len(shortlist)}] {ticker}")
        ta = ta_results[ticker]

        analyst = get_analyst_data(ticker)
        stocktwits = get_stocktwits_data(ticker, cfg)
        reddit = get_reddit_data(ticker, cfg, company_name=analyst.get("short_name"))
        if reddit.get("ok"):
            reddit_mentions[ticker] = reddit["mention_count"]

        candidates.append({
            "ticker": ticker, "region": region_of(ticker),
            "ta": ta, "analyst": analyst, "stocktwits": stocktwits, "reddit": reddit,
        })
        time.sleep(cfg.sentiment_request_delay_sec)

    # Reddit-buzz er kun meningsfuld relativt til de andre kandidater i denne
    # kørsel (rå antal opslag siger ikke meget alene) — percentilrangér.
    if reddit_mentions:
        s = pd.Series(reddit_mentions)
        reddit_pct = (s.rank(pct=True) * 100.0).to_dict()
    else:
        reddit_pct = {}

    for c in candidates:
        ticker = c["ticker"]
        pop_score = _popularity_score(c["analyst"], c["stocktwits"], reddit_pct.get(ticker))
        c["popularity_score"] = pop_score
        ta_score = c["ta"]["ta_score"]
        sent_score = pop_score if pop_score is not None else 50.0  # neutral hvis intet data
        c["composite_score"] = (
            cfg.screener_ta_weight * ta_score + cfg.screener_sentiment_weight * sent_score
        )
        upside_pct, upside_kilde = _compute_upside(c["ta"], c["analyst"])
        c["upside_pct"] = upside_pct
        c["upside_kilde"] = upside_kilde
        c["reddit_buzz_percentile"] = reddit_pct.get(ticker)

    candidates.sort(key=lambda c: c["composite_score"], reverse=True)
    return candidates[: cfg.screener_top_n]



# ============================================================================
# Fra: swing_agent/screener_report.py
# ============================================================================
"""
Genererer output/screener.html — kandidat-rangeringsrapporten.

Genbruger farvepalet og SVG-hjælpefunktioner fra report.py (samme visuelle
sprog som backtest-rapporten), tilføjer et vandret søjlediagram til at
sammenligne komposit-scores på tværs af kandidater.
"""

import os
import html as _html





def hbar_score_svg(candidates: list, width=720) -> str:
    n = len(candidates)
    row_h = 26
    pad_l, pad_r, pad_t, pad_b = 90, 40, 30, 10
    height = pad_t + pad_b + n * row_h
    plot_w = width - pad_l - pad_r

    def x_at(v):
        return pad_l + _scale(v, 0, 100, 0, plot_w)

    bars = []
    for i, c in enumerate(candidates):
        y = pad_t + i * row_h
        v = c["composite_score"]
        color = SERIES_BLUE
        bars.append(
            f'<text x="{pad_l-8}" y="{y+row_h/2+4:.1f}" text-anchor="end" font-size="11" '
            f'fill="{INK_PRIMARY}">{_html.escape(c["ticker"])}</text>'
        )
        bars.append(
            f'<rect x="{pad_l}" y="{y+4:.1f}" width="{x_at(v)-pad_l:.1f}" height="{row_h-8}" '
            f'fill="{color}" rx="3"/>'
        )
        bars.append(
            f'<text x="{x_at(v)+6:.1f}" y="{y+row_h/2+4:.1f}" font-size="10" '
            f'fill="{INK_SECONDARY}">{v:.0f}</text>'
        )

    grid = []
    for t in (0, 25, 50, 75, 100):
        x = x_at(t)
        grid.append(f'<line x1="{x:.1f}" y1="{pad_t-6}" x2="{x:.1f}" y2="{pad_t+n*row_h}" '
                     f'stroke="{GRIDLINE}" stroke-width="1"/>')
        grid.append(f'<text x="{x:.1f}" y="{pad_t-12}" text-anchor="middle" font-size="9" '
                     f'fill="{INK_MUTED}">{t}</text>')

    return f"""<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Komposit-score pr. kandidat">
      <rect x="0" y="0" width="{width}" height="{height}" fill="{SURFACE}"/>
      {''.join(grid)}
      {''.join(bars)}
    </svg>"""


def _fmt1(x, digits=1, suffix=""):
    return "–" if x is None else f"{x:.{digits}f}{suffix}"


def _analyst_cell(a: dict) -> str:
    if not a.get("ok"):
        return f'<span class="muted">Ingen data ({_html.escape(a.get("reason", ""))})</span>'
    if a.get("recommendation_key") is None and a.get("target_mean") is None:
        return '<span class="muted">Ingen analytikerdækning</span>'
    parts = []
    if a.get("recommendation_key"):
        parts.append(f'Anbefaling: <b>{_html.escape(str(a["recommendation_key"]))}</b>')
    if a.get("num_analysts"):
        parts.append(f'{a["num_analysts"]} analytikere')
    if a.get("target_mean"):
        parts.append(f'Kursmål (snit): {a["target_mean"]:.2f}')
    up, down = a.get("upgrades_90d"), a.get("downgrades_90d")
    if up is not None and down is not None:
        parts.append(f'Op-/nedgraderinger (90d): +{up}/-{down}')
    return "<br>".join(parts)


def _stocktwits_cell(s: dict) -> str:
    if not s.get("ok"):
        return f'<span class="muted">Ingen data ({_html.escape(s.get("reason", ""))})</span>'
    parts = []
    if s.get("bullish_ratio") is not None:
        parts.append(f'{s["bullish_ratio"]:.0f}% bullish (n={s["tagged_sample_size"]})')
    else:
        parts.append('Ingen sentiment-tags i seneste opslag')
    if s.get("watchlist_count"):
        parts.append(f'{s["watchlist_count"]:,} på watchlist')
    return "<br>".join(parts)


def _reddit_cell(r: dict) -> str:
    if not r.get("ok"):
        return f'<span class="muted">{_html.escape(r.get("reason", "Ingen data"))}</span>'
    if r["mention_count"] == 0:
        return '<span class="muted">Ingen omtale fundet i perioden</span>'
    return f'{r["mention_count"]} opslag (gns. score {r["avg_score"]:.0f})'


def build_screener_html(cfg: Config, candidates: list, universe_size: int) -> str:
    chart_svg = hbar_score_svg(candidates) if candidates else ""

    rows = []
    for rank, c in enumerate(candidates, 1):
        ta = c["ta"]
        triggers_html = "<br>".join(_html.escape(t) for t in ta["ta_triggers"]) or "–"
        upside_html = _fmt1(c["upside_pct"], 1, "%") if c["upside_pct"] is not None else "–"
        rows.append(f"""
      <tr>
        <td>{rank}</td>
        <td><b>{_html.escape(c['ticker'])}</b><br><span class="muted">{_html.escape(c['region'])}</span></td>
        <td>{c['composite_score']:.0f}</td>
        <td>{ta['last_price']:.2f}</td>
        <td>{upside_html}<br><span class="muted">{_html.escape(c['upside_kilde'])}</span></td>
        <td>{triggers_html}</td>
        <td>{_analyst_cell(c['analyst'])}</td>
        <td>{_stocktwits_cell(c['stocktwits'])}</td>
        <td>{_reddit_cell(c['reddit'])}</td>
      </tr>""")

    rows_html = "".join(rows) if rows else (
        '<tr><td colspan="9">Ingen kandidater bestod trendfilteret på seneste dato.</td></tr>'
    )

    return f"""<!doctype html>
<html lang="da">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Swing trading-agent — kandidat-screener</title>
<style>
  body {{ background:{PAGE}; color:{INK_PRIMARY}; font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
         max-width: 920px; margin: 0 auto; padding: 24px 16px 80px; line-height:1.5; }}
  h1 {{ font-size: 1.4rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.05rem; margin-top: 36px; border-bottom: 1px solid {GRIDLINE}; padding-bottom: 6px; }}
  .subtitle {{ color:{INK_SECONDARY}; margin-top:0; font-size:0.9rem; }}
  .chart {{ background:{SURFACE}; border:1px solid {GRIDLINE}; border-radius:10px; padding:8px; margin:14px 0; overflow-x:auto; }}
  .chart svg {{ width:100%; height:auto; display:block; min-width:480px; }}
  table {{ width:100%; border-collapse: collapse; font-size:0.8rem; margin-top:10px; display:block; overflow-x:auto; white-space:nowrap; }}
  th, td {{ text-align:left; padding:7px 9px; border-bottom:1px solid {GRIDLINE}; vertical-align:top; white-space:normal; }}
  th {{ color:{INK_MUTED}; font-weight:600; font-size:0.7rem; text-transform:uppercase; white-space:nowrap; }}
  .muted {{ color:{INK_MUTED}; font-size:0.75rem; }}
  .warn {{ background:#fff8e6; border:1px solid #f0d98c; border-radius:8px; padding:10px 14px; font-size:0.82rem; color:{INK_SECONDARY}; margin:14px 0; }}
  .assumptions li {{ margin-bottom:6px; }}
  footer {{ margin-top:44px; color:{INK_MUTED}; font-size:0.75rem; }}
</style>
</head>
<body>
  <h1>Kandidat-screener</h1>
  <p class="subtitle">TA + analytiker + sentiment-rangering · univers: {universe_size} tickere ·
  vægtning: {cfg.screener_ta_weight*100:.0f}% teknisk / {cfg.screener_sentiment_weight*100:.0f}% analytiker+sentiment
  (justér i config.py)</p>

  <div class="warn"><b>Ikke finansiel rådgivning.</b> Dette er en gennemsigtig, regelbaseret
  sammenvejning af offentligt tilgængelige data — ikke en forudsigelse eller anbefaling.
  Analytiker-kursmål og social sentiment tager jævnligt fejl. Lav altid egen research.
  StockTwits/Reddit-data er "best effort": uofficielle/rate-limitede API'er der kan mangle
  eller fejle for enkelte aktier (især ikke-amerikanske tickere har typisk ingen StockTwits-dækning),
  uden at det påvirker resten af rangeringen.</div>

  <h2>Rangering (komposit-score, 0–100)</h2>
  <div class="chart">{chart_svg}</div>

  <h2>Kandidater i detaljer</h2>
  <table>
    <tr>
      <th>#</th><th>Ticker</th><th>Score</th><th>Kurs</th><th>Upside</th>
      <th>Tekniske triggere</th><th>Analytikere</th><th>StockTwits</th><th>Reddit</th>
    </tr>
    {rows_html}
  </table>

  <h2>Sådan læses scoren</h2>
  <ul class="assumptions">
    <li><b>Teknisk score (TA)</b>: trendfilter (kurs over 200-dages glidende gennemsnit) er en
      forudsætning — herefter tillægges point for golden cross, 52-ugers breakout,
      RSI-/MACD-momentumskift og volumenspike. Kun aktier der består trendfilteret vises.</li>
    <li><b>Analytiker-score</b>: baseret på konsensus køb/hold/sælg-anbefaling og seneste
      op-/nedgraderinger (kilde: Yahoo Finance via yfinance).</li>
    <li><b>Sentiment-score</b>: StockTwits bullish/bearish-tags på seneste opslag (dæmpet mod
      neutral ved lavt antal tags), plus Reddit-omtale relativt til de andre kandidater i
      denne kørsel (kræver selv-opsat Reddit API-nøgle — se README.md).</li>
    <li><b>Upside</b>: analytiker-kursmål hvor det findes; ellers et rent teknisk estimat
      (afstand til 52-ugers højeste) markeret tydeligt som sådan — ikke et analytisk skøn.</li>
    <li>Kandidater uden analytiker-/sentiment-data får en neutral sentiment-score (50) i
      komposit-beregningen, så manglende data ikke kunstigt trækker scoren ned.</li>
  </ul>

  <footer>Genereret af swing_agent-projektet. Research-værktøj, ikke finansiel rådgivning.</footer>
</body>
</html>"""


def generate_screener_report(cfg: Config, candidates: list, universe_size: int, out_path: str):
    html_out = build_screener_html(cfg, candidates, universe_size)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html_out)
    return out_path


# ============================================================================
# Fra: swing_agent/run_screener.py
# ============================================================================
#!/usr/bin/env python3
"""
Kør kandidat-screeneren: TA + analytiker + sentiment-rangering.

Kør:
    python run_screener.py

Output: output/screener.html (åbn i browser, eller vis inline i Colab —
se README.md).

Kræver almindelig internetadgang (Yahoo Finance, evt. StockTwits/Reddit).
Bruger samme kurs-cache som run_backtest.py.
"""







def main():
    cfg = Config()
    print("=== Swing trading-agent: kandidat-screener ===")
    print(f"Shortlist: top {cfg.screener_shortlist_size} (TA) -> endelig top {cfg.screener_top_n}")
    print(f"Vægtning: {cfg.screener_ta_weight*100:.0f}% teknisk / {cfg.screener_sentiment_weight*100:.0f}% analytiker+sentiment\n")

    candidates = run_screener(cfg)
    universe_size = len(full_universe())

    out_path = generate_screener_report(cfg, candidates, universe_size, "output/screener.html")

    print("\n=== Topkandidater ===")
    for rank, c in enumerate(candidates, 1):
        upside = f"{c['upside_pct']:+.1f}%" if c["upside_pct"] is not None else "–"
        print(f"{rank:2d}. {c['ticker']:12s} score={c['composite_score']:5.1f}  "
              f"TA={c['ta']['ta_score']:5.1f}  upside={upside}")

    print(f"\nRapport gemt: {out_path}")
    print("\nHUSK: research-værktøj, ikke finansiel rådgivning.")



main()

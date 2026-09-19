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

from config import Config
from universe import region_of

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
    from strategy import rank_candidates
    from metrics import summary, quarterly_returns
    from universe import BENCHMARK

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

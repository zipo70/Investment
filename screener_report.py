"""
Genererer output/screener.html — kandidat-rangeringsrapporten.

Genbruger farvepalet og SVG-hjælpefunktioner fra report.py (samme visuelle
sprog som backtest-rapporten), tilføjer et vandret søjlediagram til at
sammenligne komposit-scores på tværs af kandidater.
"""

import os
import html as _html

from config import Config
from report import SURFACE, PAGE, INK_PRIMARY, INK_SECONDARY, INK_MUTED, GRIDLINE, BASELINE, SERIES_BLUE, STATUS_GOOD, STATUS_CRITICAL, _scale


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

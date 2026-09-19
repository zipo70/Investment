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

from universe import full_universe, BENCHMARK
from config import Config

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


if __name__ == "__main__":
    if "--check" in sys.argv:
        ok = check_environment()
        sys.exit(0 if ok else 1)

    cfg = Config()
    force = "--force" in sys.argv
    prices = load_prices(cfg, force=force)
    out_path = os.path.join("cache", "_prices_wide.csv")
    prices.to_csv(out_path)
    print(f"\nGemte samlet prisdata: {out_path}  (shape={prices.shape})")

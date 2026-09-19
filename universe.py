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

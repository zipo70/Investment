"""
trading/decision.py — Lag 2: Beslutning.

REN funktion, ingen netværk, ingen filadgang, ingen SQLite — 100%
deterministisk givet sine input. Det er bevidst: brief'en krævede at
dette lag kan unit-testes uden netværksadgang (se tests/test_decision.py),
og adskillelsen fra Lag 3 betyder at portefølje-reglerne kan ændres/
tunes/testes helt uafhængigt af Saxo-integrationen.

Reglerne her SKAL matche backtesten (config.py / strategy.py / core.py),
så det du har testet historisk er det samme der rent faktisk eksekveres:
  - Maks. cfg.max_positions (5) åbne positioner ad gangen.
  - Ligevægtet allokering: hver ny position får 1/max_positions af den
    samlede porteføljeværdi (20% ved 5 positioner) — samme som
    strategy.target_weights() med weighting="equal". (Bekræftet valg:
    "Samme som backtesten".)
  - Stop-loss følger samme princip som core.compute_trade_plan(): det
    tekniske niveau, men aldrig dybere end cfg.stop_loss_max_pct (10%)
    under indgangskursen — men selve stop-prisen beregnes i Lag 1 (den
    har adgang til kursdata via core.py) og sendes med i signalet;
    dette lag håndhæver blot at et KØB-signal har en stop-pris med,
    inden det bliver til et ordreforslag.

Lag 3 (execution) er ansvarlig for at hente den faktiske portefølje-status
fra Saxo, kalde decide() for hvert ventende signal, og eksekvere et
eventuelt OrderProposal.
"""

import hashlib
from dataclasses import dataclass, field
from typing import Dict, Literal, Optional

Direction = Literal["KØB", "SÆLG"]


def make_external_reference(ticker: str, direction: str, period_key: str) -> str:
    """Deterministisk, unik reference pr. (ticker, retning, periode).

    `period_key` bør være grovkornet nok til at gøre GENKØRSLER samme
    dag idempotente — signal_job.py bruger fx dags-datoen (ikke et fuldt
    tidsstempel), så et job der kører to gange samme dag ikke opretter to
    "forskellige" signaler for samme (ticker, retning). Saxo/db.py's
    UNIQUE-constraint på external_reference er det der reelt forhindrer
    dobbelt-indsendelse — denne funktion sikrer bare at samme
    (ticker, retning, periode) altid regner samme reference ud."""
    raw = f"{ticker}|{direction}|{period_key}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"mineaktier-{digest}"


@dataclass
class Signal:
    """Et signal fra Lag 1 (signal_job.py), læst fra trading.db."""
    ticker: str                    # Yahoo-ticker, fx "NOVO-B.CO"
    direction: Direction
    score: float                   # composite_score på signaltidspunktet
    last_price: float
    stop_price: Optional[float]    # fra core.compute_trade_plan() — kan mangle ved SÆLG
    external_reference: str


@dataclass
class PortfolioPosition:
    ticker: str
    uic: Optional[int]
    entry_price: float
    shares: float


@dataclass
class Portfolio:
    """Aktuel porteføljestatus. Lag 3 bygger denne ud fra Saxo's faktiske
    konto (via monitor.poll_once, spejlet i trading.db's positions-tabel)
    — IKKE ud fra hvad vi selv tror vi har sendt af ordrer, så en manuel
    handel foretaget direkte i Saxo også respekteres."""
    cash: float
    total_value: float
    positions: Dict[str, PortfolioPosition] = field(default_factory=dict)


@dataclass
class OrderProposal:
    """Output fra beslutningslaget — et FORSLAG, ikke en ordre. Lag 3
    omsætter dette til en faktisk Saxo-ordre (se order_executor.py)."""
    ticker: str
    direction: Direction
    external_reference: str
    notional: Optional[float] = None       # beløb at investere (KØB)
    shares_to_sell: Optional[float] = None  # antal aktier at sælge (SÆLG)
    stop_price: Optional[float] = None
    reason: str = ""


def decide(signal: Signal, portfolio: Portfolio, cfg) -> Optional[OrderProposal]:
    """Anvend porteføljereglerne på ét signal.

    Returnerer None hvis signalet skal AFVISES stille og roligt (dette er
    normal drift, ikke en fejl) — fx fordi:
      - et KØB-signal er for en aktie vi allerede har en position i
        ("ingen dobbelt-op"),
      - porteføljen allerede har cfg.max_positions åbne positioner,
      - et SÆLG-signal er for en aktie vi slet ikke har en position i.

    cfg forventes at være en config.Config-instans (eller noget med samme
    felter: max_positions, stop_loss_max_pct) — samme objekt som resten
    af projektet allerede bruger, for at undgå at reglerne kan drive fra
    hinanden."""
    if signal.direction not in ("KØB", "SÆLG"):
        raise ValueError(f"Ukendt retning: {signal.direction!r}")

    already_held = signal.ticker in portfolio.positions

    if signal.direction == "KØB":
        if already_held:
            return None
        if len(portfolio.positions) >= cfg.max_positions:
            return None
        if signal.stop_price is None or signal.stop_price <= 0:
            return None  # intet KØB uden en stop-loss-pris med i samme kald (jf. brief)
        weight = 1.0 / cfg.max_positions
        notional = portfolio.total_value * weight
        if notional <= 0 or notional > portfolio.cash:
            return None  # ikke nok kontant kapacity til en fuld ligevægtet position
        return OrderProposal(
            ticker=signal.ticker,
            direction="KØB",
            external_reference=signal.external_reference,
            notional=notional,
            stop_price=signal.stop_price,
            reason=(f"Ligevægtet indgang ({weight * 100:.0f}% af porteføljen, "
                    f"jf. backtestens {cfg.max_positions}-positioners regel)"),
        )

    # SÆLG
    if not already_held:
        return None
    pos = portfolio.positions[signal.ticker]
    return OrderProposal(
        ticker=signal.ticker,
        direction="SÆLG",
        external_reference=signal.external_reference,
        shares_to_sell=pos.shares,
        reason="Sælg hele positionen (SÆLG-signal fra Lag 1).",
    )

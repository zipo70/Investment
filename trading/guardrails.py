"""
trading/guardrails.py — Lag 3's sikkerhedsspærrer.

ALLE eksekveringsveje SKAL kalde check_guardrails() før en ordre sendes
til Saxo (order_executor.execute_order() gør dette automatisk — kald
aldrig Saxo's ordre-endpoints uden om den funktion).

Dette modul kalder ikke selv Saxo — det er ren logik + lokal tilstand
(SQLite for dagstælling, en fil for kill switch), og kan derfor også
unit-testes uden netværk.

Spærrer (jf. brief):
  - dry_run: default True. Skal SLÅS FRA eksplicit (miljøvariabel
    SAXO_DRY_RUN=false) — man skal aktivt vælge at sende rigtige ordrer,
    ikke aktivt vælge dry-run.
  - max_order_amount: største beløb (i kontoens valuta) pr. ordre.
  - max_orders_per_day: største antal ordrer pr. kalenderdag (UTC).
  - kill switch: en fil (trading/state/KILL_SWITCH) ELLER miljøvariabel
    SAXO_KILL_SWITCH=1 — findes den, stopper eksekveringen helt, uanset
    hvad signalerne siger. Aktiveres automatisk af agenten selv ved en
    uoprettelig fejl (fx OAuth-refresh der fejler for godt, se
    oauth.get_valid_access_token()), og kræver et bevidst manuelt skridt
    at fjerne igen (filen slettes ikke af sig selv).
  - notifikation ved AL ordreaktivitet: se notify.notify_order_activity()
    — kaldes for hver eneste beslutning i order_executor.py (også
    afviste/dry-run), ikke kun ved faktisk gennemførte ordrer.
"""

import datetime
import os
from dataclasses import dataclass
from pathlib import Path

KILL_SWITCH_FILE = Path(__file__).resolve().parent / "state" / "KILL_SWITCH"


@dataclass
class GuardrailConfig:
    dry_run: bool = True
    max_order_amount: float = 5_000.0     # i kontoens valuta — SÆT SELV et fornuftigt loft
    max_orders_per_day: int = 5

    @classmethod
    def from_env(cls) -> "GuardrailConfig":
        return cls(
            dry_run=os.environ.get("SAXO_DRY_RUN", "true").strip().lower() != "false",
            max_order_amount=float(os.environ.get("SAXO_MAX_ORDER_AMOUNT", 5_000.0)),
            max_orders_per_day=int(os.environ.get("SAXO_MAX_ORDERS_PER_DAY", 5)),
        )


class GuardrailViolation(Exception):
    """Rejst når en ordre IKKE må sendes. Kald-stedet skal fange denne,
    logge/notificere, og springe ordren over — ALDRIG sende den alligevel."""


def kill_switch_active() -> bool:
    if os.environ.get("SAXO_KILL_SWITCH", "").strip() == "1":
        return True
    return KILL_SWITCH_FILE.exists()


def activate_kill_switch(reason: str):
    """Kaldes af agenten selv ved en uoprettelig fejl (se
    oauth.get_valid_access_token()) — stopper al fremtidig eksekvering
    indtil filen fjernes manuelt. Bevidst: en automatisk gen-aktivering
    ville kunne skjule at noget er reelt galt."""
    KILL_SWITCH_FILE.parent.mkdir(parents=True, exist_ok=True)
    KILL_SWITCH_FILE.write_text(
        f"Kill switch aktiveret {datetime.datetime.utcnow().isoformat()}Z\n"
        f"Årsag: {reason}\n"
        "Fjern denne fil manuelt for at genoptage eksekvering, når du har "
        "undersøgt og løst årsagen.\n"
    )


def orders_placed_today(conn) -> int:
    today = datetime.datetime.utcnow().date().isoformat()
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM orders WHERE substr(placed_at, 1, 10) = ? "
        "AND status != 'error'",
        (today,),
    ).fetchone()
    return row["n"] if row else 0


def check_guardrails(conn, order_amount, gcfg: GuardrailConfig):
    """Kaster GuardrailViolation hvis ordren IKKE må sendes. Kaldes efter
    beslutningslaget (decision.decide) men FØR noget som helst sendes til
    Saxo. `order_amount` kan være None (fx et SÆLG-forslag uden notional)
    — beløbstjekket springes i så fald over, men de øvrige spærrer
    gælder stadig."""
    if kill_switch_active():
        raise GuardrailViolation("Kill switch er aktiv — ingen ordrer eksekveres.")

    if order_amount is not None and order_amount > gcfg.max_order_amount:
        raise GuardrailViolation(
            f"Ordrebeløb ({order_amount:.2f}) overstiger loftet "
            f"(max_order_amount={gcfg.max_order_amount:.2f})."
        )

    placed_today = orders_placed_today(conn)
    if placed_today >= gcfg.max_orders_per_day:
        raise GuardrailViolation(
            f"Dagens ordre-loft er nået ({placed_today}/{gcfg.max_orders_per_day})."
        )

"""
tests/test_decision.py — Unit-tests af Lag 2 (trading/decision.py).

INGEN netværksadgang, INGEN SQLite, INGEN Saxo — kun ren funktionslogik,
jf. briefens eksplicitte krav om at beslutningslaget skal kunne testes
uden netværk. Kør med:  python tests/test_decision.py
(eller `pytest tests/` hvis pytest er installeret)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import Config
from trading.decision import (
    Portfolio, PortfolioPosition, Signal, decide, make_external_reference,
)

cfg = Config()  # max_positions=5, samme regler som backtesten


def _empty_portfolio(total_value=100_000.0):
    return Portfolio(cash=total_value, total_value=total_value, positions={})


def _portfolio_with(n_positions, total_value=100_000.0, tickers=None):
    tickers = tickers or [f"TICK{i}.CO" for i in range(n_positions)]
    slot = total_value / cfg.max_positions
    positions = {
        t: PortfolioPosition(ticker=t, uic=1000 + i, entry_price=100.0, shares=slot / 100.0)
        for i, t in enumerate(tickers)
    }
    invested = sum(p.entry_price * p.shares for p in positions.values())
    return Portfolio(cash=total_value - invested, total_value=total_value, positions=positions)


# --- KØB -------------------------------------------------------------------

def test_buy_signal_on_empty_portfolio_gives_equal_weight_proposal():
    portfolio = _empty_portfolio(100_000.0)
    signal = Signal(ticker="NOVO-B.CO", direction="KØB", score=75.0,
                     last_price=800.0, stop_price=720.0,
                     external_reference=make_external_reference("NOVO-B.CO", "KØB", "2026-09-19"))
    proposal = decide(signal, portfolio, cfg)
    assert proposal is not None
    assert proposal.direction == "KØB"
    assert proposal.notional == 100_000.0 / cfg.max_positions  # 20% ved 5 positioner
    assert proposal.stop_price == 720.0
    print("test_buy_signal_on_empty_portfolio_gives_equal_weight_proposal OK —",
          proposal.notional, proposal.reason)


def test_buy_signal_rejected_when_already_held():
    portfolio = _portfolio_with(1, tickers=["NOVO-B.CO"])
    signal = Signal(ticker="NOVO-B.CO", direction="KØB", score=75.0,
                     last_price=800.0, stop_price=720.0,
                     external_reference=make_external_reference("NOVO-B.CO", "KØB", "2026-09-19"))
    proposal = decide(signal, portfolio, cfg)
    assert proposal is None
    print("test_buy_signal_rejected_when_already_held OK")


def test_buy_signal_rejected_when_portfolio_full():
    portfolio = _portfolio_with(cfg.max_positions)  # 5 positioner = fuld
    signal = Signal(ticker="NYAKTIE.CO", direction="KØB", score=80.0,
                     last_price=200.0, stop_price=180.0,
                     external_reference=make_external_reference("NYAKTIE.CO", "KØB", "2026-09-19"))
    proposal = decide(signal, portfolio, cfg)
    assert proposal is None
    print("test_buy_signal_rejected_when_portfolio_full OK")


def test_buy_signal_rejected_without_stop_price():
    portfolio = _empty_portfolio()
    signal = Signal(ticker="NOVO-B.CO", direction="KØB", score=75.0,
                     last_price=800.0, stop_price=None,
                     external_reference=make_external_reference("NOVO-B.CO", "KØB", "2026-09-19"))
    proposal = decide(signal, portfolio, cfg)
    assert proposal is None
    print("test_buy_signal_rejected_without_stop_price OK")


def test_buy_signal_rejected_when_not_enough_cash():
    # Portefølje med lav kontantbeholdning ift. total_value (fx allerede
    # investeret meget mere end de "burde" et sted decision.py ikke ved
    # om) -> notional > cash -> afvis i stedet for at overtrække.
    portfolio = Portfolio(cash=1_000.0, total_value=100_000.0, positions={})
    signal = Signal(ticker="NOVO-B.CO", direction="KØB", score=75.0,
                     last_price=800.0, stop_price=720.0,
                     external_reference=make_external_reference("NOVO-B.CO", "KØB", "2026-09-19"))
    proposal = decide(signal, portfolio, cfg)
    assert proposal is None
    print("test_buy_signal_rejected_when_not_enough_cash OK")


# --- SÆLG --------------------------------------------------------------

def test_sell_signal_on_held_position_sells_all_shares():
    portfolio = _portfolio_with(1, tickers=["NOVO-B.CO"])
    held_shares = portfolio.positions["NOVO-B.CO"].shares
    signal = Signal(ticker="NOVO-B.CO", direction="SÆLG", score=20.0,
                     last_price=700.0, stop_price=None,
                     external_reference=make_external_reference("NOVO-B.CO", "SÆLG", "2026-09-19"))
    proposal = decide(signal, portfolio, cfg)
    assert proposal is not None
    assert proposal.direction == "SÆLG"
    assert proposal.shares_to_sell == held_shares
    print("test_sell_signal_on_held_position_sells_all_shares OK —", proposal.shares_to_sell)


def test_sell_signal_rejected_when_not_held():
    portfolio = _empty_portfolio()
    signal = Signal(ticker="NOVO-B.CO", direction="SÆLG", score=20.0,
                     last_price=700.0, stop_price=None,
                     external_reference=make_external_reference("NOVO-B.CO", "SÆLG", "2026-09-19"))
    proposal = decide(signal, portfolio, cfg)
    assert proposal is None
    print("test_sell_signal_rejected_when_not_held OK")


# --- external_reference / idempotens ------------------------------------

def test_external_reference_is_deterministic_and_stable_per_day():
    ref1 = make_external_reference("NOVO-B.CO", "KØB", "2026-09-19")
    ref2 = make_external_reference("NOVO-B.CO", "KØB", "2026-09-19")
    ref3 = make_external_reference("NOVO-B.CO", "KØB", "2026-09-20")  # ny dag -> ny reference
    ref4 = make_external_reference("NOVO-B.CO", "SÆLG", "2026-09-19")  # anden retning -> ny reference
    assert ref1 == ref2
    assert ref1 != ref3
    assert ref1 != ref4
    print("test_external_reference_is_deterministic_and_stable_per_day OK —", ref1)


def test_invalid_direction_raises():
    portfolio = _empty_portfolio()
    signal = Signal(ticker="X.CO", direction="HOLD", score=50.0,  # type: ignore[arg-type]
                     last_price=100.0, stop_price=90.0,
                     external_reference="dummy")
    try:
        decide(signal, portfolio, cfg)
    except ValueError:
        print("test_invalid_direction_raises OK")
        return
    raise AssertionError("Forventede ValueError for ukendt retning")


if __name__ == "__main__":
    test_buy_signal_on_empty_portfolio_gives_equal_weight_proposal()
    test_buy_signal_rejected_when_already_held()
    test_buy_signal_rejected_when_portfolio_full()
    test_buy_signal_rejected_without_stop_price()
    test_buy_signal_rejected_when_not_enough_cash()
    test_sell_signal_on_held_position_sells_all_shares()
    test_sell_signal_rejected_when_not_held()
    test_external_reference_is_deterministic_and_stable_per_day()
    test_invalid_direction_raises()
    print("\nALLE DECISION-TESTS BESTÅET (ingen netværk brugt)")

"""
tests/test_saxo_execution.py — Netværksfri tests af Saxo-eksekveringslaget
(trading/saxo_api.py, order_executor.py, instrument_mapping.py, oauth.py,
run_execution_cycle.py). Alle Saxo-kald er mocket — INGEN netværk.

Kør med:  python tests/test_saxo_execution.py   (eller `pytest tests/`)."""

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("NTFY_TOPIC", "")  # ingen rigtige notifikationer under test

from trading import db, guardrails, saxo_api, order_executor  # noqa: E402
from trading.decision import OrderProposal  # noqa: E402
from trading.instrument_mapping import filter_candidates, split_yahoo_ticker  # noqa: E402
from trading.oauth import extract_code  # noqa: E402
from trading.run_execution_cycle import build_portfolio, split_fresh_signals  # noqa: E402
import datetime  # noqa: E402


# --- Antal aktier og valuta ---------------------------------------------------

def test_share_amount_rounds_down():
    # 20.000 DKK / (100 USD × 6,90 DKK/USD) = 28,98 -> 28 aktier
    assert order_executor.compute_share_amount(20_000, 100.0, 6.90) == 28


def test_share_amount_same_currency():
    assert order_executor.compute_share_amount(20_000, 500.0, 1.0) == 40


def test_share_amount_never_exceeds_notional():
    n = order_executor.compute_share_amount(10_000, 333.33, 1.0)
    assert n * 333.33 <= 10_000


def test_share_amount_handles_missing_data():
    assert order_executor.compute_share_amount(0, 100, 1) == 0
    assert order_executor.compute_share_amount(1000, 0, 1) == 0
    assert order_executor.compute_share_amount(1000, 100, None) == 0


# --- Ordre-body ---------------------------------------------------------------

def test_buy_body_has_required_fields_and_related_stop():
    b = order_executor.build_buy_body(
        account_key="AK1", uic=211, asset_type="Stock", shares=28,
        stop_price=90.5, stop_order_type="StopIfTraded", external_reference="mineaktier-abc",
    )
    assert b["Amount"] == 28 and b["ManualOrder"] is False and b["BuySell"] == "Buy"
    stop = b["Orders"][0]
    assert stop["OrderType"] == "StopIfTraded" and stop["BuySell"] == "Sell"
    assert stop["Amount"] == 28 and stop["OrderPrice"] == 90.5 and stop["ManualOrder"] is False
    assert len(stop["ExternalReference"]) <= 50  # Saxos grænse


def test_sell_body():
    b = order_executor.build_sell_body(account_key="AK1", uic=211, asset_type="Stock",
                                       shares=28, external_reference="mineaktier-x")
    assert b["BuySell"] == "Sell" and b["OrderType"] == "Market" and "Orders" not in b


def test_find_stop_orders_only_matching_uic_and_stop_type():
    orders = [
        {"OrderId": "1", "Uic": 211, "BuySell": "Sell", "OpenOrderType": "StopIfTraded"},
        {"OrderId": "2", "Uic": 211, "BuySell": "Buy", "OpenOrderType": "Limit"},
        {"OrderId": "3", "Uic": 999, "BuySell": "Sell", "OpenOrderType": "StopIfTraded"},
        {"OrderId": "4", "Uic": 211, "BuySell": "Sell", "OrderType": "Stop"},
    ]
    assert order_executor.find_stop_orders_for_uic(orders, 211) == ["1", "4"]


# --- Precheck, tick, stop-type --------------------------------------------------

def test_precheck_ok_with_rate():
    r = saxo_api.parse_precheck(200, {"PreCheckResult": "Ok", "InstrumentToAccountConversionRate": 6.9})
    assert r["ok"] and r["conversion_rate"] == 6.9


def test_precheck_error_in_200_response():
    r = saxo_api.parse_precheck(200, {"ErrorInfo": {"ErrorCode": "InsufficientFunds", "Message": "Ikke nok"}})
    assert not r["ok"] and "Ikke nok" in r["error"]


def test_precheck_http_error():
    r = saxo_api.parse_precheck(400, {"Message": "Bad request"})
    assert not r["ok"] and r["error"] == "Bad request"


def test_precheck_unaccepted_disclaimer_blocks():
    r = saxo_api.parse_precheck(200, {"PreCheckResult": "Ok",
                                      "PreTradeDisclaimers": {"DisclaimerTokens": ["t1"]}})
    assert not r["ok"] and "disclaimer" in r["error"]


def test_round_to_tick_simple_and_scheme():
    assert saxo_api.round_to_tick(90.537, {"TickSize": 0.05}) == 90.5
    scheme = {"TickSizeScheme": {"DefaultTickSize": 1.0,
                                 "Elements": [{"HighPrice": 50, "TickSize": 0.01},
                                              {"HighPrice": 500, "TickSize": 0.1}]}}
    assert saxo_api.round_to_tick(123.47, scheme) == 123.4
    assert saxo_api.round_to_tick(12.349, scheme) == 12.34
    assert saxo_api.round_to_tick(1234.5, scheme) == 1234.0
    assert saxo_api.round_to_tick(12.345, {}) == 12.35 or saxo_api.round_to_tick(12.345, {}) == 12.34


def test_choose_stop_order_type():
    assert saxo_api.choose_stop_order_type({"SupportedOrderTypes": ["Market", "Limit", "StopIfTraded"]}) == "StopIfTraded"
    assert saxo_api.choose_stop_order_type({"SupportedOrderTypes": ["Market", "Stop"]}) == "Stop"
    assert saxo_api.choose_stop_order_type({}) == "StopIfTraded"


# --- Kapital og positioner ------------------------------------------------------

def test_strategy_capital_caps_big_sim_account():
    c = saxo_api.strategy_capital({"total_value": 1_000_000, "cash": 1_000_000}, 100_000)
    assert c == {"total_value": 100_000, "cash": 100_000}


def test_strategy_capital_accounts_for_invested():
    c = saxo_api.strategy_capital({"total_value": 1_000_000, "cash": 960_000}, 100_000)
    assert c["total_value"] == 100_000 and c["cash"] == 60_000


def test_strategy_capital_small_account_not_inflated():
    c = saxo_api.strategy_capital({"total_value": 50_000, "cash": 30_000}, 100_000)
    assert c == {"total_value": 50_000, "cash": 30_000}


def test_aggregate_positions_merges_same_uic():
    raw = [
        {"PositionBase": {"Uic": 211, "AssetType": "Stock", "Amount": 10, "OpenPrice": 100,
                          "Status": "Open", "ExecutionTimeOpen": "2026-10-02T10:00:00Z"}},
        {"PositionBase": {"Uic": 211, "AssetType": "Stock", "Amount": 10, "OpenPrice": 110,
                          "Status": "Open", "ExecutionTimeOpen": "2026-10-01T10:00:00Z"}},
        {"PositionBase": {"Uic": 300, "AssetType": "Stock", "Amount": 0, "OpenPrice": 50}},
        {"PositionBase": {"Uic": 400, "AssetType": "Stock", "Amount": 5, "OpenPrice": 50, "Status": "Closed"}},
    ]
    agg = saxo_api.aggregate_positions(raw)
    assert list(agg) == [211]
    assert agg[211]["shares"] == 20 and abs(agg[211]["entry_price"] - 105) < 1e-9
    assert agg[211]["entry_date"] == "2026-10-01T10:00:00Z"


def test_build_portfolio_uses_capital():
    p = build_portfolio({"AAPL": {"uic": 211, "entry_price": 100, "shares": 10}},
                        {"total_value": 100_000, "cash": 80_000})
    assert p.total_value == 100_000 and p.cash == 80_000 and "AAPL" in p.positions


def test_stale_signals_are_split_off_and_fresh_sorted():
    now = datetime.datetime(2026, 10, 25, 12, 0)
    rows = [
        {"external_reference": "old", "direction": "KØB", "score": 99, "created_at": "2026-09-21T11:00:00"},
        {"external_reference": "lo", "direction": "KØB", "score": 60, "created_at": "2026-10-25T09:00:00"},
        {"external_reference": "hi", "direction": "KØB", "score": 80, "created_at": "2026-10-24T11:30:00"},
        {"external_reference": "sell", "direction": "SÆLG", "score": 10, "created_at": "2026-10-25T09:00:00"},
        {"external_reference": "bad", "direction": "KØB", "score": 50, "created_at": "ikke-en-dato"},
    ]
    fresh, stale = split_fresh_signals(rows, now, 36)
    assert [r["external_reference"] for r in fresh] == ["sell", "hi", "lo"]
    assert sorted(r["external_reference"] for r in stale) == ["bad", "old"]


# --- Instrument-opslag ---------------------------------------------------------

def test_split_yahoo_ticker():
    assert split_yahoo_ticker("NOVO-B.CO") == ("NOVO", "B", "CO")
    assert split_yahoo_ticker("AAPL") == ("AAPL", None, "")


def test_filter_candidates_picks_right_exchange_and_class():
    results = [
        {"Symbol": "NOVOb:xcse", "Identifier": 15629, "AssetType": "Stock"},
        {"Symbol": "NOVO:xnys", "Identifier": 999, "AssetType": "Stock"},
        {"Symbol": "NOVOb:xetr", "Identifier": 888, "AssetType": "Stock"},
    ]
    m = filter_candidates("NOVO-B.CO", results)
    assert [r["Identifier"] for r in m] == [15629]


def test_filter_candidates_us_listing():
    results = [{"Symbol": "AAPL:xnas", "Identifier": 211}, {"Symbol": "AAPL:xetr", "Identifier": 5}]
    assert [r["Identifier"] for r in filter_candidates("AAPL", results)] == [211]


def test_filter_candidates_rejects_similar_symbols():
    results = [{"Symbol": "AAPLX:xnas", "Identifier": 1}]
    assert filter_candidates("AAPL", results) == []


# --- OAuth ---------------------------------------------------------------------

def test_extract_code_from_full_url():
    url = "http://localhost:12321/callback?code=1a2b3c4d-1111-2222-3333-444455556666&state=mineaktier-1"
    assert extract_code(url) == "1a2b3c4d-1111-2222-3333-444455556666"


def test_extract_code_plain():
    assert extract_code("  abc-123 \n") == "abc-123"


# --- Hele execute_order i dry-run med mocket Saxo -----------------------------

def _proposal(direction="KØB"):
    return OrderProposal(ticker="AAPL", direction=direction, external_reference=f"mineaktier-test{direction}",
                         notional=20_000.0 if direction == "KØB" else None,
                         shares_to_sell=None if direction == "KØB" else 28,
                         stop_price=180.123, reason="test")


def test_execute_order_dry_run_buy_sends_nothing():
    with tempfile.TemporaryDirectory() as d, db.connect(Path(d) / "t.db") as conn:
        db.insert_signal(conn, external_reference="mineaktier-testKØB", ticker="AAPL",
                         direction="KØB", score=70, last_price=200.0, stop_price=180.123)
        with mock.patch.object(saxo_api, "precheck",
                               return_value={"ok": True, "error": None, "conversion_rate": 6.9, "raw": {}}), \
             mock.patch.object(saxo_api, "get_instrument_details",
                               return_value={"SupportedOrderTypes": ["StopIfTraded"], "TickSize": 0.01}), \
             mock.patch.object(saxo_api, "place_order") as place, \
             mock.patch.object(order_executor, "notify_order_activity"):
            res = order_executor.execute_order(
                conn, "tok", _proposal(), uic=211, asset_type="Stock",
                account={"account_key": "AK1"}, last_price=200.0,
                gcfg=guardrails.GuardrailConfig(dry_run=True, max_order_amount=25_000),
            )
            place.assert_not_called()
        assert res["status"] == "dry_run"
        assert res["shares"] == 14  # 20.000 / (200 × 6,9) = 14,49
        assert res["body"]["Orders"][0]["OrderPrice"] == 180.12
        row = conn.execute("SELECT status FROM signals WHERE external_reference='mineaktier-testKØB'").fetchone()
        assert row["status"] == "ordered"


def test_execute_order_live_sell_cancels_stop_first():
    calls = []
    with tempfile.TemporaryDirectory() as d, db.connect(Path(d) / "t.db") as conn:
        db.insert_signal(conn, external_reference="mineaktier-testSÆLG", ticker="AAPL",
                         direction="SÆLG", score=20, last_price=200.0, stop_price=None)
        with mock.patch.object(saxo_api, "list_open_orders", return_value=[
                {"OrderId": "77", "Uic": 211, "BuySell": "Sell", "OpenOrderType": "StopIfTraded"}]), \
             mock.patch.object(saxo_api, "cancel_orders", side_effect=lambda *a: calls.append(("cancel", a[2]))), \
             mock.patch.object(saxo_api, "precheck",
                               side_effect=lambda *a: calls.append(("precheck",)) or
                               {"ok": True, "error": None, "conversion_rate": None, "raw": {}}), \
             mock.patch.object(saxo_api, "place_order",
                               side_effect=lambda *a: calls.append(("place",)) or {"OrderId": "555"}), \
             mock.patch.object(order_executor, "notify_order_activity"):
            res = order_executor.execute_order(
                conn, "tok", _proposal("SÆLG"), uic=211, asset_type="Stock",
                account={"account_key": "AK1"}, last_price=200.0,
                gcfg=guardrails.GuardrailConfig(dry_run=False, max_order_amount=25_000),
            )
        assert [c[0] for c in calls] == ["cancel", "precheck", "place"]
        assert calls[0][1] == ["77"]
        assert res["saxo_order_id"] == "555"


def test_execute_order_blocks_on_failed_precheck():
    with tempfile.TemporaryDirectory() as d, db.connect(Path(d) / "t.db") as conn:
        db.insert_signal(conn, external_reference="mineaktier-testKØB", ticker="AAPL",
                         direction="KØB", score=70, last_price=200.0, stop_price=180.0)
        prechecks = iter([
            {"ok": True, "error": None, "conversion_rate": 1.0, "raw": {}},
            {"ok": False, "error": "InsufficientFunds", "conversion_rate": None, "raw": {}},
        ])
        with mock.patch.object(saxo_api, "precheck", side_effect=lambda *a: next(prechecks)), \
             mock.patch.object(saxo_api, "get_instrument_details", return_value={}), \
             mock.patch.object(saxo_api, "place_order") as place, \
             mock.patch.object(order_executor, "notify_order_activity"):
            try:
                order_executor.execute_order(
                    conn, "tok", _proposal(), uic=211, asset_type="Stock",
                    account={"account_key": "AK1"}, last_price=200.0,
                    gcfg=guardrails.GuardrailConfig(dry_run=False, max_order_amount=25_000),
                )
                raise AssertionError("burde have rejst ExecutionError")
            except order_executor.ExecutionError as e:
                assert "InsufficientFunds" in str(e)
            place.assert_not_called()


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_") and callable(v)]
    for t in tests:
        t()
        print(f"OK  {t.__name__}")
    print(f"\n{len(tests)} tests bestået (ingen netværk brugt)")

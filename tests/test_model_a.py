import datetime as dt

import numpy as np
import pandas as pd

import model_a


def test_rebalance_schedule():
    assert model_a.rebalance_info(dt.date(2026, 10, 26))["is_rebalance_day"]
    assert not model_a.rebalance_info(dt.date(2026, 10, 27))["is_rebalance_day"]
    assert not model_a.rebalance_info(dt.date(2026, 10, 25))["is_rebalance_day"]
    nxt = model_a.rebalance_info(dt.date(2026, 10, 27))["next"]
    assert model_a.rebalance_info(nxt)["is_rebalance_day"]


def test_signals_trend_and_momentum():
    idx = pd.bdate_range(end="2026-10-23", periods=300)
    up = pd.DataFrame({"Close": np.linspace(50, 150, 300)}, index=idx)
    s = model_a.signals_for_df(up)
    assert s["ok"] and s["trend_ok"] and s["momentum"] > 0
    down = pd.DataFrame({"Close": np.linspace(150, 50, 300)}, index=idx)
    s = model_a.signals_for_df(down)
    assert s["ok"] and not s["trend_ok"] and s["momentum"] < 0


def test_action_for():
    rd = {"top": ["A"], "by_ticker": {"A": {"rank": 1}, "B": {"rank": 7}},
          "rebalance": {"is_rebalance_day": False, "next": dt.date(2027, 1, 21)}}
    ok = {"ok": True, "trend_ok": True, "momentum": 0.3, "last": 10, "sma200": 9}
    assert model_a.action_for("A", ok, rd)["action"] == "KØB"
    assert model_a.action_for("B", ok, rd)["action"] == "HOLD"
    assert model_a.action_for("B", dict(ok, trend_ok=False), rd)["action"] == "SÆLG"

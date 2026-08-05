from __future__ import annotations

from types import SimpleNamespace
import unittest

from trading_lab.runners.mt5.rsiqui_final import PaperOnlyRsiquiMt5Runner, load_config


class FakeMt5:
    ACCOUNT_TRADE_MODE_PAPER = 0
    TIMEFRAME_M5 = 5
    TRADE_ACTION_DEAL = 1
    ORDER_TYPE_BUY = 0
    ORDER_TYPE_SELL = 1
    ORDER_TIME_GTC = 0
    ORDER_FILLING_FOK = 0
    TRADE_RETCODE_DONE = 10009

    def __init__(self) -> None:
        self.selected_symbols: list[str] = []
        self.position_symbols: list[str] = []

    def initialize(self) -> bool:
        return True

    def last_error(self):
        return (0, "ok")

    def shutdown(self) -> None:
        pass

    def account_info(self):
        return SimpleNamespace(trade_mode=self.ACCOUNT_TRADE_MODE_PAPER, equity=10_000.0)

    def symbol_select(self, symbol: str, enabled: bool) -> bool:
        self.selected_symbols.append(symbol)
        return True

    def symbol_info(self, symbol: str):
        return SimpleNamespace(
            volume_min=0.01,
            volume_max=10.0,
            volume_step=0.01,
            point=0.01,
            digits=2,
            trade_tick_size=0.01,
            trade_tick_value=1.0,
            trade_stops_level=0,
            filling_mode=1,
        )

    def symbol_info_tick(self, symbol: str):
        return SimpleNamespace(bid=2300.00, ask=2300.10)

    def positions_get(self, symbol: str):
        self.position_symbols.append(symbol)
        return ()


class RsiquiFinalSingleSymbolContractTests(unittest.TestCase):
    def test_loads_final_contract_as_single_xauusd_symbol(self) -> None:
        config = load_config("configs/strategies/rsiqui/final_m5.json")

        self.assertEqual("XAUUSD", config.symbol)
        self.assertEqual(0.03, config.volume_lots)
        self.assertEqual(100.0, config.price_value_per_lot)
        self.assertEqual(30.0, config.risk_usd)
        self.assertEqual(7.5, config.reward_usd)
        self.assertEqual(0.4, config.max_spread_price)
        self.assertEqual("02:00", config.blackout_start_gmt7)
        self.assertEqual("08:29", config.blackout_until_gmt7)
        self.assertIsNone(config.equity_risk_cap_pct)


    def test_final_runner_uses_xauusd_only(self) -> None:
        config = load_config("configs/strategies/rsiqui/final_m5.json")
        runner = PaperOnlyRsiquiMt5Runner(config, mt5=FakeMt5())

        self.assertEqual("XAUUSD", runner._active_symbol())
        self.assertEqual(("XAUUSD",), runner._symbols_required_for_start())
        self.assertEqual(("XAUUSD",), runner._symbols_to_guard())
        self.assertTrue(runner.start())
        self.assertEqual(["XAUUSD"], runner.mt5.selected_symbols)

    def test_build_request_uses_weekday_final_money_contract_only(self) -> None:
        config = load_config("configs/strategies/rsiqui/final_m5.json")
        runner = PaperOnlyRsiquiMt5Runner(config, mt5=FakeMt5())

        self.assertTrue(runner.start())
        request = runner._build_request("long")
        strategy_config = runner._strategy_config()

        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual("XAUUSD", request["symbol"])
        self.assertEqual(0.03, request["volume"])
        self.assertAlmostEqual(10.0, request["price"] - request["sl"], places=2)
        self.assertAlmostEqual(2.5, request["tp"] - request["price"], places=2)
        self.assertEqual(0.03, strategy_config.volume_lots)
        self.assertEqual(100.0, strategy_config.price_value_per_lot)
        self.assertEqual(30.0, strategy_config.risk_usd)
        self.assertEqual(7.5, strategy_config.reward_usd)
        self.assertEqual(0.4, strategy_config.max_spread)

    def test_position_guard_checks_xauusd_only(self) -> None:
        mt5 = FakeMt5()
        runner = PaperOnlyRsiquiMt5Runner(load_config("configs/strategies/rsiqui/final_m5.json"), mt5=mt5)

        self.assertFalse(runner._open_positions_exist())
        self.assertEqual(["XAUUSD"], mt5.position_symbols)


if __name__ == "__main__":
    unittest.main()

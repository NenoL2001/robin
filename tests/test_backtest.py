from portfolio_bot.backtest import BacktestStore, default_demo_prices, run_equity_backtest, run_long_call_backtest
from portfolio_bot.memory import MemoryStore


def test_equity_backtest_saves_result_and_memory(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite")
    store = BacktestStore(tmp_path / "backtest.sqlite", memory=memory)
    result = run_equity_backtest(
        default_demo_prices(),
        strategy_name="semiconductor_reversal",
        strategy_version="1.0.0",
    )

    store.save(result)

    recent = store.recent(strategy_name="semiconductor_reversal")
    assert len(recent) == 1
    assert recent[0].trade_count > 0
    assert "price_count" in recent[0].metadata
    assert memory.recent(kind="backtest_result", strategy="semiconductor_reversal")


def test_long_call_backtest_outputs_option_metrics():
    result = run_long_call_backtest(
        [8, 15, 20],
        strike=13,
        premium=3.5,
        contracts=1,
        strategy_name="semiconductor_reversal",
    )

    assert result.asset_type == "option"
    assert result.trade_count == 1
    assert result.total_return == 1
    assert result.metadata["max_loss"] == 350
    assert result.metadata["breakeven"] == 16.5

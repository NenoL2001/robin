import pytest
from concurrent.futures import ThreadPoolExecutor

from portfolio_bot.memory import MemoryStore
from portfolio_bot.paper import PaperBroker


def test_paper_option_buy_sell_uses_multiplier_and_memory(tmp_path):
    memory = MemoryStore(tmp_path / "memory.sqlite")
    broker = PaperBroker(tmp_path / "paper.sqlite", starting_cash=1000, memory=memory)

    buy = broker.buy(
        symbol="POET_2027_13C",
        asset_type="option",
        quantity=2,
        price=3,
        strategy_name="semiconductor_reversal",
        strategy_version="1.0.0",
        signal_id="sig-1",
        reason="cheap long call",
        memory_context="POET catalyst memory",
    )

    assert buy.gross_value == 600
    assert buy.multiplier == 100
    assert broker.cash() == 400
    position = broker.get_position("POET_2027_13C")
    assert position is not None
    assert position.cost_basis == 600

    broker.sell(
        symbol="POET_2027_13C",
        asset_type="option",
        quantity=1,
        price=4,
        strategy_name="semiconductor_reversal",
        strategy_version="1.0.0",
        signal_id="sig-2",
        reason="trim into strength",
    )

    assert broker.cash() == 800
    assert broker.get_position("POET_2027_13C").quantity == 1
    assert len(memory.recent(kind="paper_order")) == 2


def test_paper_rejects_invalid_orders(tmp_path):
    broker = PaperBroker(tmp_path / "paper.sqlite", starting_cash=100)

    with pytest.raises(ValueError, match="数量"):
        broker.buy(
            symbol="AEHR",
            asset_type="equity",
            quantity=-1,
            price=10,
            strategy_name="semiconductor_reversal",
            strategy_version="1.0.0",
            signal_id="sig-1",
            reason="invalid",
        )

    with pytest.raises(ValueError, match="现金不足"):
        broker.buy(
            symbol="AEHR",
            asset_type="equity",
            quantity=20,
            price=10,
            strategy_name="semiconductor_reversal",
            strategy_version="1.0.0",
            signal_id="sig-2",
            reason="too large",
        )

    with pytest.raises(ValueError, match="持仓不足"):
        broker.sell(
            symbol="AEHR",
            asset_type="equity",
            quantity=1,
            price=10,
            strategy_name="semiconductor_reversal",
            strategy_version="1.0.0",
            signal_id="sig-3",
            reason="no position",
        )

    with pytest.raises(ValueError, match="strategy_name"):
        broker.buy(
            symbol="AEHR",
            asset_type="equity",
            quantity=1,
            price=10,
            strategy_name="",
            strategy_version="1.0.0",
            signal_id="sig-4",
            reason="missing strategy",
        )


def test_paper_concurrent_buys_cannot_overspend(tmp_path):
    path = tmp_path / "paper.sqlite"
    PaperBroker(path, starting_cash=100)

    def buy_once(index):
        broker = PaperBroker(path, starting_cash=100)
        try:
            broker.buy(
                symbol="AEHR",
                asset_type="equity",
                quantity=1,
                price=60,
                strategy_name="semiconductor_reversal",
                strategy_version="1.0.0",
                signal_id=f"sig-{index}",
                reason="concurrent test",
            )
            return True
        except ValueError:
            return False

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(buy_once, [1, 2]))

    broker = PaperBroker(path, starting_cash=100)
    assert results.count(True) == 1
    assert broker.cash() == 40
    assert broker.get_position("AEHR").quantity == 1

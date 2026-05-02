from portfolio_bot.config import load_config
from portfolio_bot.backtest import BacktestResult, BacktestStore
from portfolio_bot.orchestrator import promote_candidate_strategies, run_code_iteration_review


def test_code_iteration_dry_run_does_not_write_review_files(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(config_path)

    path = run_code_iteration_review(config, iterations=1, dry_run=True)

    assert str(path).endswith(".md")
    assert not (config.data_dir / "code_iterations").exists()
    assert not (config.data_dir / "pr_drafts").exists()


def test_candidate_strategy_auto_promotes_only_after_gates_pass(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    policy_dir = tmp_path / "system_skills" / "code_iteration"
    policy_dir.mkdir(parents=True)
    (policy_dir / "policy.yaml").write_text(
        """
auto_strategy_activation: true
strategy_activation_gates:
  require_py_compile: true
  require_pytest: true
  require_strategy_dry_run: true
  min_backtest_trades: 8
  max_backtest_drawdown: -0.35
""",
        encoding="utf-8",
    )
    skill_dir = tmp_path / "strategy_skills" / "ai_compute_candidate"
    skill_dir.mkdir(parents=True)
    strategy_file = skill_dir / "strategy.yaml"
    strategy_file.write_text(
        """
name: ai_compute_candidate
version: 1.0.0
status: candidate
description: test candidate
calculation:
  module: portfolio_bot.strategies.semiconductor_reversal
  class: SemiconductorReversalStrategy
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    BacktestStore(config.data_dir / config.backtest.sqlite_path).save(
        BacktestResult(
            backtest_id="bt-pass",
            strategy_name="ai_compute_candidate",
            strategy_version="1.0.0",
            asset_type="equity",
            total_return=0.2,
            max_drawdown=-0.1,
            win_rate=0.6,
            trade_count=8,
            average_trade_return=0.02,
            losing_trades=2,
            metadata={},
        )
    )

    result = promote_candidate_strategies(
        config,
        {"returncode": 0},
        {"returncode": 0},
        {"returncode": 0},
        dry_run=False,
    )

    assert result["promoted"][0]["strategy"] == "ai_compute_candidate"
    assert "status: active" in strategy_file.read_text(encoding="utf-8")

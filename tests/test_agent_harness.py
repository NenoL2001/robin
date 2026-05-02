from __future__ import annotations

import pytest

from portfolio_bot.agents.harness.guardrails import AgentGuardrails
from portfolio_bot.agents.harness.memory import MemorySynthesizer
from portfolio_bot.agents.harness.runner import HarnessRunner
from portfolio_bot.agents.harness.schemas import ToolCallRequest, parse_agent_plan
from portfolio_bot.config import load_config
from portfolio_bot.memory import MemoryStore, memory_path
from portfolio_bot.runtime import RuntimeStore, runtime_path


def write_config(tmp_path, extra: str = ""):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        f"""
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
timezone: UTC
notifications:
  imessage_enabled: false
memory:
  enabled: true
{extra}
""",
        encoding="utf-8",
    )
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    return load_config(config_path)


def test_agent_plan_schema_rejects_missing_keys():
    with pytest.raises(ValueError, match="missing required keys"):
        parse_agent_plan({"plan": "do work", "tasks": [], "tool_calls": []})


def test_agent_guardrails_block_sensitive_paths_and_real_trading(tmp_path):
    config = write_config(tmp_path)
    guardrails = AgentGuardrails(config.root)

    env_verdict = guardrails.check_tool_call(
        "maintenance_agent",
        ToolCallRequest("code_patch", {"path": ".env"}, "bad"),
        dry_run=True,
    )
    broker_verdict = guardrails.check_tool_call(
        "strategy_agent",
        ToolCallRequest("real_broker_order", {"symbol": "POET"}, "bad"),
        dry_run=True,
    )
    wrong_agent_verdict = guardrails.check_tool_call(
        "strategy_agent",
        ToolCallRequest("code_patch", {"path": "portfolio_bot/foo.py"}, "bad"),
        dry_run=True,
    )

    assert not env_verdict.allowed
    assert not broker_verdict.allowed
    assert not wrong_agent_verdict.allowed


def test_agent_memory_synthesis_marks_stale_and_low_confidence(tmp_path):
    config = write_config(tmp_path)
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=True)
    memory.add(
        "strategy_lesson",
        "POET long call failed because catalyst slipped.",
        symbol="POET",
        strategy="semiconductor_reversal",
        confidence=0.35,
        source="test",
        expires_at="2020-01-01T00:00:00+00:00",
    )

    bundle = MemorySynthesizer(config.root, runtime, memory).synthesize("POET long call", symbols=["POET"], limit=5)

    assert bundle.reflective
    assert any("low-confidence" in item or "expired" in item for item in bundle.stale_warnings)


def test_harness_runner_executes_deterministic_agent_and_persists_trace(tmp_path):
    config = write_config(tmp_path)
    result = HarnessRunner(config).run("operator_agent", "check workers", dry_run=True)
    runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
    trace = runtime.agent_trace(result.run_id)

    assert result.status == "done"
    assert result.verification["ok"] is True
    assert len(trace["tasks"]) == 3
    assert {row["tool_name"] for row in trace["tool_calls"]} == {"runtime_status", "workers_status"}
    assert trace["reflections"]


def test_harness_runner_executes_langgraph_engine_and_persists_trace(tmp_path):
    pytest.importorskip("langgraph")
    pytest.importorskip("langgraph.checkpoint.sqlite")
    config = write_config(
        tmp_path,
        """
agent_harness:
  engine: langgraph
""",
    )
    result = HarnessRunner(config).run("operator_agent", "check workers", dry_run=True)
    trace = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).agent_trace(result.run_id)
    span_names = {row["span_name"] for row in trace["trace_spans"]}
    checkpoint_spans = [row for row in trace["trace_spans"] if row["span_name"] == "langgraph_checkpoint"]

    assert result.status == "done"
    assert result.verification["ok"] is True
    assert "langgraph_checkpoint" in span_names
    assert checkpoint_spans[0]["metadata"]["checkpointer"] == "sqlite"
    assert (config.data_dir / "agent_harness" / "langgraph_checkpoints.sqlite").exists()
    assert {row["tool_name"] for row in trace["tool_calls"]} == {"runtime_status", "workers_status"}


def test_harness_budget_failure_is_persisted(tmp_path):
    config = write_config(
        tmp_path,
        """
agent_harness:
  max_tool_calls: 0
""",
    )
    result = HarnessRunner(config).run("operator_agent", "budget failure", dry_run=True)
    trace = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)).agent_trace(result.run_id)

    assert result.status == "failed"
    assert "budget exceeded" in result.reflection["summary"]
    assert trace["run"]["status"] == "failed"

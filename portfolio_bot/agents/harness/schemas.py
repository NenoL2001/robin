from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentBudget:
    max_turns: int = 4
    max_tool_calls: int = 8
    max_run_seconds: int = 300
    max_auto_changed_files: int = 3
    max_patch_lines: int = 250


@dataclass(slots=True)
class AgentSpec:
    name: str
    role: str
    objective: str
    model: str
    budget: AgentBudget
    guardrails: list[str] = field(default_factory=list)


@dataclass(slots=True)
class AgentTask:
    task_id: str
    name: str
    description: str
    status: str = "pending"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolCallRequest:
    tool_name: str
    input: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    untrusted: bool = False


@dataclass(slots=True)
class VerificationResult:
    ok: bool
    summary: str
    checks: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AgentPlan:
    plan: str
    tasks: list[AgentTask]
    tool_calls: list[ToolCallRequest]
    verification: dict[str, Any] = field(default_factory=dict)
    reflection: dict[str, Any] = field(default_factory=dict)
    memory_writes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MemoryBundle:
    query: str
    factual: list[dict[str, Any]] = field(default_factory=list)
    episodic: list[dict[str, Any]] = field(default_factory=list)
    procedural: list[dict[str, Any]] = field(default_factory=list)
    reflective: list[dict[str, Any]] = field(default_factory=list)
    stale_warnings: list[str] = field(default_factory=list)

    def to_prompt_context(self) -> str:
        lines = [f"Memory query: {self.query}"]
        for name in ("factual", "episodic", "procedural", "reflective"):
            rows = getattr(self, name)
            if rows:
                lines.append(f"{name}:")
                lines.extend(f"- {row.get('content', row)}" for row in rows[:8])
        if self.stale_warnings:
            lines.append("stale_warnings:")
            lines.extend(f"- {warning}" for warning in self.stale_warnings[:8])
        return "\n".join(lines)


@dataclass(slots=True)
class ToolExecutionResult:
    tool_name: str
    status: str
    output: dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(slots=True)
class AgentRunResult:
    run_id: int
    status: str
    agent_name: str
    objective: str
    plan: dict[str, Any]
    tool_results: list[dict[str, Any]]
    verification: dict[str, Any]
    reflection: dict[str, Any]


def parse_agent_plan(payload: dict[str, Any]) -> AgentPlan:
    required = {"plan", "tasks", "tool_calls", "verification", "reflection", "memory_writes"}
    missing = sorted(required - set(payload))
    if missing:
        raise ValueError(f"agent plan missing required keys: {', '.join(missing)}")
    if not isinstance(payload["plan"], str) or not payload["plan"].strip():
        raise ValueError("agent plan requires non-empty plan text")
    if not isinstance(payload["tasks"], list):
        raise ValueError("agent plan tasks must be a list")
    if not isinstance(payload["tool_calls"], list):
        raise ValueError("agent plan tool_calls must be a list")
    tasks = [parse_task(item, index) for index, item in enumerate(payload["tasks"])]
    tool_calls = [parse_tool_call(item) for item in payload["tool_calls"]]
    for key in ("verification", "reflection"):
        if not isinstance(payload[key], dict):
            raise ValueError(f"agent plan {key} must be an object")
    if not isinstance(payload["memory_writes"], list):
        raise ValueError("agent plan memory_writes must be a list")
    return AgentPlan(
        plan=payload["plan"].strip(),
        tasks=tasks,
        tool_calls=tool_calls,
        verification=payload["verification"],
        reflection=payload["reflection"],
        memory_writes=payload["memory_writes"],
    )


def parse_task(value: Any, index: int) -> AgentTask:
    if not isinstance(value, dict):
        raise ValueError("agent plan task must be an object")
    task_id = str(value.get("task_id") or f"task-{index + 1}")
    name = str(value.get("name") or task_id)
    description = str(value.get("description") or "")
    if not description.strip():
        raise ValueError(f"agent plan task {task_id} requires description")
    return AgentTask(task_id=task_id, name=name, description=description, status=str(value.get("status", "pending")), metadata=dict(value.get("metadata", {}) or {}))


def parse_tool_call(value: Any) -> ToolCallRequest:
    if not isinstance(value, dict):
        raise ValueError("agent plan tool call must be an object")
    tool_name = str(value.get("tool_name") or value.get("name") or "")
    if not tool_name:
        raise ValueError("agent plan tool call requires tool_name")
    payload = value.get("input", {})
    if not isinstance(payload, dict):
        raise ValueError(f"agent plan tool call {tool_name} input must be an object")
    return ToolCallRequest(
        tool_name=tool_name,
        input=payload,
        reason=str(value.get("reason", "")),
        untrusted=bool(value.get("untrusted", False)),
    )


def default_agent_specs(config) -> dict[str, AgentSpec]:
    budget = AgentBudget(
        max_turns=config.agent_harness.max_turns,
        max_tool_calls=config.agent_harness.max_tool_calls,
        max_run_seconds=config.agent_harness.max_run_seconds,
        max_auto_changed_files=config.agent_harness.max_auto_changed_files,
        max_patch_lines=config.agent_harness.max_patch_lines,
    )
    model = config.agent_harness.default_model
    deep = config.agent_harness.deep_model
    return {
        "operator_agent": AgentSpec("operator_agent", "operator", "协调 worker、job、健康状态和日程。", model, budget, ["no_real_trading", "no_secret_output"]),
        "research_agent": AgentSpec("research_agent", "research", "检查新闻源、去重、中文证据链和数据缺口。", model, budget, ["untrusted_news", "link_evidence_required"]),
        "strategy_agent": AgentSpec("strategy_agent", "strategy", "复盘策略状态、feature 需求、信号质量和候选策略。", deep, budget, ["candidate_before_active", "paper_only"]),
        "risk_agent": AgentSpec("risk_agent", "risk", "检查真实持仓、大行情、期权风险和告警降噪。", model, budget, ["paper_only", "no_real_trading"]),
        "report_agent": AgentSpec("report_agent", "report", "汇总中文日报、agent 状态和待验证问题。", model, budget, ["chinese_output", "no_direct_trade_instruction"]),
        "maintenance_agent": AgentSpec("maintenance_agent", "maintenance", "运行 profile、测试、PR 草稿和受限代码迭代 review。", deep, budget, ["code_iteration_policy", "bounded_patch", "verification_required"]),
        "verification_agent": AgentSpec("verification_agent", "verification", "运行测试、profile、dry-run 和安全边界检查。", model, budget, ["verification_required", "no_secret_output"]),
    }

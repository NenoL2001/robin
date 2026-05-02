from __future__ import annotations

from dataclasses import asdict
from typing import Any

from .schemas import AgentPlan, AgentSpec, AgentTask, ToolCallRequest


class DeterministicPlanner:
    """Local planner used when no model is available or deterministic tests are needed."""

    def plan(self, spec: AgentSpec, objective: str, observation: dict[str, Any], memory_context: str, *, dry_run: bool) -> dict[str, Any]:
        calls = default_tool_calls(spec.name, objective, dry_run=dry_run)
        tasks = [
            AgentTask("observe", "观察当前状态", "读取 runtime、memory、持仓或策略状态，形成最小上下文。"),
            AgentTask("execute", "执行受限工具", "按 guardrail 执行确定性工具，不触碰真实交易。"),
            AgentTask("verify", "验证结果", "检查工具状态、预算和安全边界，并写入反思记忆。"),
        ]
        plan = AgentPlan(
            plan=f"{spec.name} 使用确定性 harness 处理目标：{objective or spec.objective}",
            tasks=tasks,
            tool_calls=calls,
            verification={"required": True, "mode": "deterministic"},
            reflection={"required": True, "memory_layer": "reflective"},
            memory_writes=[{"kind": "agent_reflection", "source": spec.name, "confidence": 0.75}],
        )
        return asdict(plan)


def default_tool_calls(agent_name: str, objective: str, *, dry_run: bool) -> list[ToolCallRequest]:
    query = objective or agent_name
    if agent_name == "operator_agent":
        return [
            ToolCallRequest("runtime_status", {}, "检查 job/runtime 状态"),
            ToolCallRequest("workers_status", {}, "检查 worker heartbeat"),
        ]
    if agent_name == "research_agent":
        return [
            ToolCallRequest("data_news", {"symbols": [], "days": 3}, "从 DataHub 抽样新闻并验证去重链路"),
            ToolCallRequest("memory_search", {"query": query, "limit": 6}, "检索相关新闻和历史线索"),
        ]
    if agent_name == "strategy_agent":
        return [
            ToolCallRequest("strategy_news_scout", {"symbols": [], "dry_run": dry_run}, "策略自主补充官方源和网页证据"),
            ToolCallRequest("factor_iterate", {"dry_run": dry_run}, "每日新增/更新因子候选和权重账本"),
            ToolCallRequest("strategy_review", {"dry_run": dry_run}, "复盘策略 skill、paper 和回测状态"),
            ToolCallRequest("source_config_review", {"dry_run": dry_run}, "检查是否需要启用已实现的信息源配置"),
            ToolCallRequest("memory_search", {"query": "strategy lesson feature signal", "limit": 8}, "查找策略经验记忆"),
        ]
    if agent_name == "risk_agent":
        return [
            ToolCallRequest("runtime_status", {}, "检查事件和队列"),
            ToolCallRequest("memory_search", {"query": "market_event risk option portfolio", "limit": 8}, "检索风险记忆"),
        ]
    if agent_name == "report_agent":
        return [
            ToolCallRequest("agent_status_summary", {}, "生成日报中的 Agent 运行状态段落"),
            ToolCallRequest("memory_search", {"query": "daily_review agent_reflection", "limit": 8}, "查找日报上下文"),
        ]
    if agent_name == "maintenance_agent":
        calls = [
            ToolCallRequest("profile_suite", {"iterations": 1, "dry_run": True}, "先获取 profile 证据"),
            ToolCallRequest("code_iteration_review", {"iterations": 1, "dry_run": dry_run}, "生成受 policy 约束的 PR 草稿和 review"),
        ]
        if not dry_run:
            calls.append(ToolCallRequest("code_patch", {"proposal": "low_risk_only"}, "记录低风险自动补丁门禁"))
        return calls
    if agent_name == "verification_agent":
        return [
            ToolCallRequest("test_py_compile", {}, "验证 Python 文件可编译"),
            ToolCallRequest("agent_doctor", {}, "检查 harness guardrail/runtime 状态"),
        ]
    return [ToolCallRequest("runtime_status", {}, "默认检查 runtime 状态")]

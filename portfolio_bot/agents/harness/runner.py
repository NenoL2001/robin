from __future__ import annotations

import time
from contextlib import nullcontext
from dataclasses import asdict
from typing import Any

from ...config import BotConfig
from ...memory import MemoryStore, memory_path
from ...runtime import RuntimeStore, runtime_path
from ...storage import load_holdings
from .guardrails import AgentGuardrails
from .memory import MemorySynthesizer
from .planner import DeterministicPlanner
from .schemas import AgentRunResult, ToolExecutionResult, default_agent_specs, parse_agent_plan
from .tools import ToolRegistry
from .tracing import trace_span


class HarnessRunner:
    def __init__(self, config: BotConfig, planner: DeterministicPlanner | None = None):
        self.config = config
        self.runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        self.planner = planner or DeterministicPlanner()
        self.guardrails = AgentGuardrails(config.root)

    def run(self, agent_name: str, objective: str = "", *, dry_run: bool = False) -> AgentRunResult:
        specs = default_agent_specs(self.config)
        if agent_name not in specs:
            raise ValueError(f"unknown agent: {agent_name}")
        if not self.config.agent_harness.enabled:
            raise RuntimeError("agent_harness is disabled")
        spec = specs[agent_name]
        objective = objective or spec.objective
        run_id = self.runtime.start_agent_run(
            spec.name,
            spec.role,
            objective,
            metadata={
                "budget": asdict(spec.budget),
                "guardrails": spec.guardrails,
                "model": spec.model,
                "dry_run": dry_run,
                "engine": self.config.agent_harness.engine,
                "thread_id": f"agent_run:{agent_name}:pending",
                "langgraph_available": langgraph_available(),
            },
        )
        thread_id = f"agent_run:{run_id}"
        self.runtime.add_agent_trace_span(run_id, "workflow_engine", "done", f"engine={self.config.agent_harness.engine}", {"thread_id": thread_id, "langgraph_available": langgraph_available()})
        if self.config.agent_harness.engine == "langgraph" and langgraph_available():
            return self._run_with_langgraph(run_id, thread_id, spec, agent_name, objective, dry_run=dry_run)
        started = time.monotonic()
        plan_dict: dict[str, Any] = {}
        tool_results: list[ToolExecutionResult] = []
        verification: dict[str, Any] = {}
        reflection: dict[str, Any] = {}
        try:
            with trace_span(self.runtime, run_id, "observe"):
                observation = self.observe(agent_name, objective)
            with trace_span(self.runtime, run_id, "memory_synthesis"):
                bundle = MemorySynthesizer(self.config.root, self.runtime, self.memory).synthesize(
                    objective,
                    symbols=observation.get("symbols", []),
                    strategy=observation.get("strategy", ""),
                    run_id=run_id,
                    limit=self.config.memory.max_context_items,
                )
            with trace_span(self.runtime, run_id, "plan"):
                plan = parse_agent_plan(self.planner.plan(spec, objective, observation, bundle.to_prompt_context(), dry_run=dry_run))
                plan_dict = plan.to_dict()
                for task in plan.tasks:
                    self.runtime.add_agent_task(run_id, task.task_id, task.name, task.description, task.status, task.metadata)
            if len(plan.tool_calls) > spec.budget.max_tool_calls:
                raise RuntimeError(f"agent budget exceeded: tool_calls={len(plan.tool_calls)} max={spec.budget.max_tool_calls}")
            with trace_span(self.runtime, run_id, "guardrail"):
                verdict = self.guardrails.check_plan(agent_name, plan, dry_run=dry_run)
                self.runtime.add_agent_trace_span(run_id, "guardrail_verdict", "done" if verdict.allowed else "blocked", "guardrail verdict", verdict.to_dict())
                if not verdict.allowed:
                    raise RuntimeError("agent guardrail blocked plan: " + "; ".join(verdict.reasons))
            with trace_span(self.runtime, run_id, "execute_tools"):
                registry = ToolRegistry(self.config, self.runtime, self.memory, dry_run=dry_run)
                for call in plan.tool_calls:
                    if time.monotonic() - started > spec.budget.max_run_seconds:
                        raise RuntimeError("agent budget exceeded: max_run_seconds")
                    verdict = self.guardrails.check_tool_call(agent_name, call, dry_run=dry_run)
                    if not verdict.allowed:
                        result = ToolExecutionResult(call.tool_name, "blocked", {"guardrail": verdict.to_dict()})
                        self.runtime.add_agent_tool_call(run_id, call.tool_name, "blocked", call.input, result.output)
                        tool_results.append(result)
                        continue
                    try:
                        output = registry.execute(call.tool_name, call.input)
                        result = ToolExecutionResult(call.tool_name, "done", output)
                        self.runtime.add_agent_tool_call(run_id, call.tool_name, "done", call.input, output)
                    except Exception as exc:
                        result = ToolExecutionResult(call.tool_name, "failed", {}, str(exc))
                        self.runtime.add_agent_tool_call(run_id, call.tool_name, "failed", call.input, {}, error=str(exc))
                    tool_results.append(result)
            with trace_span(self.runtime, run_id, "verify"):
                verification = self.verify(tool_results)
                if self.config.agent_harness.require_verification and not verification["ok"]:
                    raise RuntimeError("agent verification failed: " + verification["summary"])
            with trace_span(self.runtime, run_id, "reflect"):
                reflection = self.reflect(agent_name, objective, tool_results, verification, bundle.stale_warnings)
                self.runtime.add_agent_reflection(run_id, "final", reflection["summary"], reflection)
                memory_id = self.memory.add(
                    "agent_reflection",
                    reflection["summary"],
                    strategy=agent_name,
                    importance=0.65,
                    confidence=0.75 if verification.get("ok") else 0.45,
                    source="agent_harness",
                    metadata=reflection,
                    related_run_id=run_id,
                )
                self.runtime.add_agent_memory_link(run_id, memory_id, "reflective", reflection["summary"], 0.75, reflection)
            payload = AgentRunResult(
                run_id=run_id,
                status="done",
                agent_name=agent_name,
                objective=objective,
                plan=plan_dict,
                tool_results=[asdict(result) for result in tool_results],
                verification=verification,
                reflection=reflection,
            )
            self.runtime.update_agent_run(run_id, status="done", result=asdict(payload))
            return payload
        except Exception as exc:
            reflection = {"summary": f"{agent_name} 失败: {exc}", "ok": False, "tool_count": len(tool_results)}
            self.runtime.add_agent_reflection(run_id, "failure", reflection["summary"], reflection)
            self.memory.add(
                "agent_reflection",
                reflection["summary"],
                strategy=agent_name,
                importance=0.55,
                confidence=0.8,
                source="agent_harness",
                metadata=reflection,
                related_run_id=run_id,
            )
            payload = AgentRunResult(
                run_id=run_id,
                status="failed",
                agent_name=agent_name,
                objective=objective,
                plan=plan_dict,
                tool_results=[asdict(result) for result in tool_results],
                verification=verification,
                reflection=reflection,
            )
            self.runtime.update_agent_run(run_id, status="failed", result=asdict(payload), error=str(exc))
            return payload

    def _run_with_langgraph(
        self,
        run_id: int,
        thread_id: str,
        spec,
        agent_name: str,
        objective: str,
        *,
        dry_run: bool,
    ) -> AgentRunResult:
        from langgraph.graph import END, StateGraph

        started = time.monotonic()
        state: dict[str, Any] = {
            "plan_dict": {},
            "tool_results": [],
            "verification": {},
            "reflection": {},
            "stale_warnings": [],
        }

        def observe_node(current: dict[str, Any]) -> dict[str, Any]:
            current = dict(current)
            with trace_span(self.runtime, run_id, "observe"):
                current["observation"] = self.observe(agent_name, objective)
            return current

        def memory_node(current: dict[str, Any]) -> dict[str, Any]:
            current = dict(current)
            with trace_span(self.runtime, run_id, "memory_synthesis"):
                bundle = MemorySynthesizer(self.config.root, self.runtime, self.memory).synthesize(
                    objective,
                    symbols=current.get("observation", {}).get("symbols", []),
                    strategy=current.get("observation", {}).get("strategy", ""),
                    run_id=run_id,
                    limit=self.config.memory.max_context_items,
                )
                current["memory_context"] = bundle.to_prompt_context()
                current["stale_warnings"] = list(bundle.stale_warnings)
            return current

        def plan_node(current: dict[str, Any]) -> dict[str, Any]:
            current = dict(current)
            with trace_span(self.runtime, run_id, "plan"):
                plan = parse_agent_plan(
                    self.planner.plan(
                        spec,
                        objective,
                        current.get("observation", {}),
                        str(current.get("memory_context", "")),
                        dry_run=dry_run,
                    )
                )
                current["plan"] = plan
                current["plan_dict"] = plan.to_dict()
                for task in plan.tasks:
                    self.runtime.add_agent_task(run_id, task.task_id, task.name, task.description, task.status, task.metadata)
            if len(current["plan"].tool_calls) > spec.budget.max_tool_calls:
                raise RuntimeError(f"agent budget exceeded: tool_calls={len(current['plan'].tool_calls)} max={spec.budget.max_tool_calls}")
            return current

        def guardrail_node(current: dict[str, Any]) -> dict[str, Any]:
            current = dict(current)
            with trace_span(self.runtime, run_id, "guardrail"):
                verdict = self.guardrails.check_plan(agent_name, current["plan"], dry_run=dry_run)
                self.runtime.add_agent_trace_span(run_id, "guardrail_verdict", "done" if verdict.allowed else "blocked", "guardrail verdict", verdict.to_dict())
                if not verdict.allowed:
                    raise RuntimeError("agent guardrail blocked plan: " + "; ".join(verdict.reasons))
            return current

        def execute_node(current: dict[str, Any]) -> dict[str, Any]:
            current = dict(current)
            tool_results: list[ToolExecutionResult] = []
            with trace_span(self.runtime, run_id, "execute_tools"):
                registry = ToolRegistry(self.config, self.runtime, self.memory, dry_run=dry_run)
                for call in current["plan"].tool_calls:
                    if time.monotonic() - started > spec.budget.max_run_seconds:
                        raise RuntimeError("agent budget exceeded: max_run_seconds")
                    verdict = self.guardrails.check_tool_call(agent_name, call, dry_run=dry_run)
                    if not verdict.allowed:
                        result = ToolExecutionResult(call.tool_name, "blocked", {"guardrail": verdict.to_dict()})
                        self.runtime.add_agent_tool_call(run_id, call.tool_name, "blocked", call.input, result.output)
                        tool_results.append(result)
                        continue
                    try:
                        output = registry.execute(call.tool_name, call.input)
                        result = ToolExecutionResult(call.tool_name, "done", output)
                        self.runtime.add_agent_tool_call(run_id, call.tool_name, "done", call.input, output)
                    except Exception as exc:
                        result = ToolExecutionResult(call.tool_name, "failed", {}, str(exc))
                        self.runtime.add_agent_tool_call(run_id, call.tool_name, "failed", call.input, {}, error=str(exc))
                    tool_results.append(result)
            current["tool_results"] = tool_results
            return current

        def verify_node(current: dict[str, Any]) -> dict[str, Any]:
            current = dict(current)
            with trace_span(self.runtime, run_id, "verify"):
                current["verification"] = self.verify(current.get("tool_results", []))
                if self.config.agent_harness.require_verification and not current["verification"]["ok"]:
                    raise RuntimeError("agent verification failed: " + current["verification"]["summary"])
            return current

        def reflect_node(current: dict[str, Any]) -> dict[str, Any]:
            current = dict(current)
            with trace_span(self.runtime, run_id, "reflect"):
                reflection = self.reflect(
                    agent_name,
                    objective,
                    current.get("tool_results", []),
                    current.get("verification", {}),
                    current.get("stale_warnings", []),
                )
                current["reflection"] = reflection
                self.runtime.add_agent_reflection(run_id, "final", reflection["summary"], reflection)
                memory_id = self.memory.add(
                    "agent_reflection",
                    reflection["summary"],
                    strategy=agent_name,
                    importance=0.65,
                    confidence=0.75 if current.get("verification", {}).get("ok") else 0.45,
                    source="agent_harness",
                    metadata=reflection,
                    related_run_id=run_id,
                )
                self.runtime.add_agent_memory_link(run_id, memory_id, "reflective", reflection["summary"], 0.75, reflection)
            return current

        graph = StateGraph(dict)
        graph.add_node("observe", observe_node)
        graph.add_node("memory", memory_node)
        graph.add_node("plan", plan_node)
        graph.add_node("guardrail", guardrail_node)
        graph.add_node("execute_tools", execute_node)
        graph.add_node("verify", verify_node)
        graph.add_node("reflect", reflect_node)
        graph.set_entry_point("observe")
        graph.add_edge("observe", "memory")
        graph.add_edge("memory", "plan")
        graph.add_edge("plan", "guardrail")
        graph.add_edge("guardrail", "execute_tools")
        graph.add_edge("execute_tools", "verify")
        graph.add_edge("verify", "reflect")
        graph.add_edge("reflect", END)

        try:
            checkpointer_context, checkpoint_meta = self._langgraph_checkpointer()
            with checkpointer_context as checkpointer:
                app = graph.compile(checkpointer=checkpointer)
                self.runtime.add_agent_trace_span(run_id, "langgraph_checkpoint", "done", "LangGraph workflow compiled with thread checkpoint", {"thread_id": thread_id, **checkpoint_meta, "audit_store": "runtime_sqlite"})
                state = app.invoke(state, config={"configurable": {"thread_id": thread_id}})
            payload = AgentRunResult(
                run_id=run_id,
                status="done",
                agent_name=agent_name,
                objective=objective,
                plan=state.get("plan_dict", {}),
                tool_results=[asdict(result) for result in state.get("tool_results", [])],
                verification=state.get("verification", {}),
                reflection=state.get("reflection", {}),
            )
            self.runtime.update_agent_run(run_id, status="done", result=asdict(payload))
            return payload
        except Exception as exc:
            tool_results = state.get("tool_results", [])
            reflection = {"summary": f"{agent_name} 失败: {exc}", "ok": False, "tool_count": len(tool_results)}
            self.runtime.add_agent_reflection(run_id, "failure", reflection["summary"], reflection)
            self.memory.add(
                "agent_reflection",
                reflection["summary"],
                strategy=agent_name,
                importance=0.55,
                confidence=0.8,
                source="agent_harness",
                metadata=reflection,
                related_run_id=run_id,
            )
            payload = AgentRunResult(
                run_id=run_id,
                status="failed",
                agent_name=agent_name,
                objective=objective,
                plan=state.get("plan_dict", {}),
                tool_results=[asdict(result) for result in tool_results],
                verification=state.get("verification", {}),
                reflection=reflection,
            )
            self.runtime.update_agent_run(run_id, status="failed", result=asdict(payload), error=str(exc))
            return payload

    def _langgraph_checkpointer(self):
        try:
            from langgraph.checkpoint.sqlite import SqliteSaver

            checkpoint_path = self.config.data_dir / "agent_harness" / "langgraph_checkpoints.sqlite"
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            return SqliteSaver.from_conn_string(str(checkpoint_path)), {"checkpointer": "sqlite", "checkpoint_path": str(checkpoint_path)}
        except Exception as exc:
            from langgraph.checkpoint.memory import InMemorySaver

            return nullcontext(InMemorySaver()), {"checkpointer": "memory", "checkpoint_error": str(exc)}

    def observe(self, agent_name: str, objective: str) -> dict[str, Any]:
        holdings = load_holdings(self.config.holdings_path)
        symbols = []
        for holding in holdings:
            if holding.asset_type == "option":
                underlying = str(holding.metadata.get("underlying", "")).strip().upper()
                if underlying:
                    symbols.append(underlying)
            elif holding.asset_type in {"equity", "etf"}:
                symbols.append(holding.normalized_symbol())
        return {
            "agent_name": agent_name,
            "objective": objective,
            "symbols": sorted(set(symbols)),
            "runtime": self.runtime.status(),
            "recent_runs": self.runtime.recent_agent_runs(limit=5),
        }

    @staticmethod
    def verify(tool_results: list[ToolExecutionResult]) -> dict[str, Any]:
        failed = [result for result in tool_results if result.status == "failed"]
        blocked = [result for result in tool_results if result.status == "blocked"]
        ok = not failed and not blocked
        checks = [{"tool": result.tool_name, "status": result.status, "error": result.error} for result in tool_results]
        if ok:
            summary = f"verification passed; tools={len(tool_results)}"
        else:
            summary = f"verification failed; failed={len(failed)} blocked={len(blocked)}"
        return {"ok": ok, "summary": summary, "checks": checks}

    @staticmethod
    def reflect(agent_name: str, objective: str, tool_results: list[ToolExecutionResult], verification: dict[str, Any], stale_warnings: list[str]) -> dict[str, Any]:
        completed = [result.tool_name for result in tool_results if result.status == "done"]
        summary = (
            f"{agent_name} 完成目标：{objective}。"
            f"工具完成 {len(completed)}/{len(tool_results)}，验证={'通过' if verification.get('ok') else '未通过'}。"
        )
        if stale_warnings:
            summary += f" 发现 {len(stale_warnings)} 条过期或低置信记忆需要复核。"
        return {"summary": summary, "completed_tools": completed, "verification": verification, "stale_warnings": stale_warnings}


def run_agent(config: BotConfig, agent_name: str, objective: str = "", *, dry_run: bool = False) -> AgentRunResult:
    return HarnessRunner(config).run(agent_name, objective, dry_run=dry_run)


def langgraph_available() -> bool:
    try:
        import langgraph  # noqa: F401

        return True
    except Exception:
        return False

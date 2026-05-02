from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..runtime import RuntimeStore, runtime_path


AgentStep = Callable[["LocalAgentContext"], dict[str, Any]]


@dataclass(slots=True)
class AgentSpec:
    name: str
    role: str
    objective: str
    budget: dict[str, Any] = field(default_factory=dict)
    guardrails: list[str] = field(default_factory=list)


@dataclass(slots=True)
class LocalAgentContext:
    config: BotConfig
    runtime: RuntimeStore
    memory: MemoryStore
    run_id: int
    spec: AgentSpec

    def remember_step(self, step_name: str, status: str, summary: str, metadata: dict[str, Any] | None = None) -> None:
        self.runtime.add_agent_step(self.run_id, step_name, status, summary, metadata or {})


class LocalAgentRunner:
    """Small deterministic agent runtime for local workers.

    The runner gives each agent an objective, bounded steps, memory access,
    runtime logging, and a final result. Model calls can be steps, but the
    control loop itself remains deterministic and auditable.
    """

    def __init__(self, config: BotConfig, spec: AgentSpec):
        self.config = config
        self.spec = spec
        self.runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)

    def run(self, steps: list[tuple[str, AgentStep]]) -> dict[str, Any]:
        run_id = self.runtime.start_agent_run(
            self.spec.name,
            self.spec.role,
            self.spec.objective,
            metadata={"budget": self.spec.budget, "guardrails": self.spec.guardrails},
        )
        context = LocalAgentContext(self.config, self.runtime, self.memory, run_id, self.spec)
        results: dict[str, Any] = {}
        try:
            for step_name, step in steps:
                try:
                    result = step(context)
                    results[step_name] = result
                    context.remember_step(step_name, "done", str(result.get("summary", "done"))[:1000], result)
                except Exception as exc:
                    context.remember_step(step_name, "failed", str(exc), {})
                    raise
            self.runtime.update_agent_run(run_id, status="done", result=results)
            self.memory.add(
                "daily_review",
                f"Agent {self.spec.name} completed objective: {self.spec.objective}",
                strategy=self.spec.name,
                importance=0.6,
                confidence=0.75,
                source="agent_runtime",
                metadata={"run_id": run_id, "steps": list(results)},
            )
            return {"run_id": run_id, "status": "done", "results": results}
        except Exception as exc:
            self.runtime.update_agent_run(run_id, status="failed", error=str(exc), result=results)
            self.memory.add(
                "daily_review",
                f"Agent {self.spec.name} failed objective: {exc}",
                strategy=self.spec.name,
                importance=0.5,
                confidence=0.8,
                source="agent_runtime",
                metadata={"run_id": run_id, "steps": list(results)},
            )
            raise

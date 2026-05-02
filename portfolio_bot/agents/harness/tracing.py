from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from ...runtime import RuntimeStore


@contextmanager
def trace_span(runtime: RuntimeStore, run_id: int, span_name: str, metadata: dict[str, Any] | None = None) -> Iterator[None]:
    runtime.add_agent_trace_span(run_id, span_name, "started", f"{span_name} started", metadata or {})
    try:
        yield
    except Exception as exc:
        runtime.add_agent_trace_span(run_id, span_name, "failed", str(exc), metadata or {})
        raise
    else:
        runtime.add_agent_trace_span(run_id, span_name, "done", f"{span_name} done", metadata or {})

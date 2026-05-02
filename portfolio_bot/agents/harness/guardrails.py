from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .schemas import AgentPlan, ToolCallRequest


@dataclass(slots=True)
class GuardrailVerdict:
    allowed: bool
    reasons: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "reasons": self.reasons}


class AgentGuardrails:
    def __init__(self, root: Path, policy_path: Path | None = None):
        self.root = root
        self.policy = load_policy(policy_path or root / "system_skills" / "code_iteration" / "policy.yaml")

    def check_plan(self, agent_name: str, plan: AgentPlan, *, dry_run: bool) -> GuardrailVerdict:
        reasons = []
        if len(plan.tool_calls) == 0:
            reasons.append("plan has no tool calls; agent must gather or verify something")
        for call in plan.tool_calls:
            verdict = self.check_tool_call(agent_name, call, dry_run=dry_run)
            if not verdict.allowed:
                reasons.extend(verdict.reasons)
        return GuardrailVerdict(not reasons, reasons)

    def check_tool_call(self, agent_name: str, call: ToolCallRequest, *, dry_run: bool) -> GuardrailVerdict:
        reasons = []
        payload = call.input
        text = f"{call.tool_name} {payload}".lower()
        if call.tool_name == "code_patch" and agent_name != "maintenance_agent":
            reasons.append("code_patch is restricted to maintenance_agent")
        if call.tool_name == "code_patch" and not self.policy.get("proposal_rules", {}).get("allow_auto_code_edit", False):
            reasons.append("code_patch disabled by code_iteration policy")
        if call.tool_name == "code_patch" and not dry_run and not self.policy.get("auto_source_edits", False):
            reasons.append("non-dry-run code_patch disabled by policy")
        if call.tool_name in {"real_broker_order", "robinhood_order", "broker_order"}:
            reasons.append("real broker trading tools are forbidden")
        if "real_broker" in text or "robinhood_order" in text or "真实下单" in text:
            reasons.append("real broker trading intent is forbidden")
        if "imessage_recipient" in text or "email_to" in text or "notification_recipient" in text:
            reasons.append("notification recipient changes are forbidden")
        for path in self._candidate_paths(payload):
            path_reason = self._blocked_path_reason(path)
            if path_reason:
                reasons.append(path_reason)
        if call.untrusted and call.tool_name in {"code_patch", "memory_add"}:
            reasons.append("untrusted evidence cannot directly write code or durable memory")
        return GuardrailVerdict(not reasons, reasons)

    def _candidate_paths(self, payload: Any) -> list[str]:
        paths: list[str] = []
        if isinstance(payload, dict):
            for key, value in payload.items():
                if key in {"path", "file", "target", "paths", "files"}:
                    if isinstance(value, list):
                        paths.extend(str(item) for item in value)
                    else:
                        paths.append(str(value))
                else:
                    paths.extend(self._candidate_paths(value))
        elif isinstance(payload, list):
            for item in payload:
                paths.extend(self._candidate_paths(item))
        return paths

    def _blocked_path_reason(self, value: str) -> str:
        normalized = value.replace("\\", "/").strip()
        if not normalized:
            return ""
        if normalized == ".env" or normalized.endswith("/.env"):
            return f"blocked path: {normalized}"
        for blocked in self.policy.get("blocked_paths", []) or []:
            blocked_norm = str(blocked).replace("\\", "/").strip()
            if blocked_norm and (normalized == blocked_norm or normalized.endswith(f"/{blocked_norm}")):
                return f"blocked by policy path: {normalized}"
        return ""


def load_policy(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError:
        raw = {}
    return raw if isinstance(raw, dict) else {}

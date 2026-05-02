from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from ..config import BotConfig, load_config
from ..memory import MemoryStore, memory_path
from ..runtime import RuntimeStore, runtime_path


ALLOWED_SOURCE_KEYS = {
    "research.web_search_enabled",
    "research.web_search_provider",
    "research.web_search_api_provider",
    "research.web_search_max_results",
    "research.web_search_timeout_seconds",
    "strategy_research.enabled",
    "strategy_research.max_queries_per_strategy",
    "strategy_research.official_sources_first",
    "strategy_research.secondary_search_on_gap",
    "strategy_lab.daily_factor_iteration_enabled",
    "strategy_lab.allow_new_factor_candidates",
    "strategy_lab.min_factor_observations_for_orders",
}


@dataclass(slots=True)
class SourceConfigChangeResult:
    changed: bool
    dry_run: bool
    applied_updates: dict[str, Any] = field(default_factory=dict)
    backup_path: str = ""
    validation_ok: bool = False
    restart_requested: bool = False
    restart_ok: bool = False
    error: str = ""


class SourceConfigManager:
    def __init__(self, config: BotConfig):
        self.config = config
        self.runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)

    def ensure_default_sources(self, *, dry_run: bool = True) -> SourceConfigChangeResult:
        if self.config.research.web_search_enabled:
            return SourceConfigChangeResult(changed=False, dry_run=dry_run, validation_ok=True)
        if not self.config.agent_harness.auto_source_config_enabled:
            return SourceConfigChangeResult(changed=False, dry_run=dry_run, validation_ok=True)
        return self.apply_updates({"research.web_search_enabled": True}, dry_run=dry_run, restart=self.config.agent_harness.auto_restart_after_source_config)

    def apply_updates(self, updates: dict[str, Any], *, dry_run: bool = True, restart: bool = False) -> SourceConfigChangeResult:
        clean_updates = {key: value for key, value in updates.items() if key in ALLOWED_SOURCE_KEYS}
        rejected = sorted(set(updates) - set(clean_updates))
        if rejected:
            return SourceConfigChangeResult(changed=False, dry_run=dry_run, error=f"unsupported source config keys: {', '.join(rejected)}")
        config_path = self.config.config_path
        if not config_path:
            return SourceConfigChangeResult(changed=False, dry_run=dry_run, error="config path is unavailable")
        if dry_run:
            return SourceConfigChangeResult(changed=bool(clean_updates), dry_run=True, applied_updates=clean_updates, validation_ok=True)
        backup = backup_config(config_path)
        result = SourceConfigChangeResult(changed=False, dry_run=False, applied_updates=clean_updates, backup_path=str(backup))
        try:
            raw = read_yaml_mapping(config_path)
            for key, value in clean_updates.items():
                set_nested(raw, key.split("."), value)
            config_path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
            load_config(config_path)
            validation = subprocess.run([sys.executable, "-m", "portfolio_bot", "--config", str(config_path), "validate-config"], cwd=str(self.config.root), text=True, capture_output=True, timeout=60)
            if validation.returncode != 0:
                raise RuntimeError((validation.stdout + validation.stderr)[-1000:])
            result.changed = True
            result.validation_ok = True
            if restart:
                result.restart_requested = True
                restart_result = restart_launchd("com.noah.portfolio-bot")
                result.restart_ok = restart_result.returncode == 0
                if not result.restart_ok:
                    result.error = (restart_result.stdout + restart_result.stderr)[-1000:]
        except Exception as exc:
            shutil.copy2(backup, config_path)
            result.error = str(exc)
            result.validation_ok = False
            result.restart_ok = False
        self._remember(result)
        return result

    def _remember(self, result: SourceConfigChangeResult) -> None:
        self.memory.add(
            "source_config_change",
            f"source config change changed={result.changed} validation={result.validation_ok} restart={result.restart_ok}",
            strategy="strategy_agent",
            importance=0.7 if result.changed else 0.45,
            confidence=0.75 if result.validation_ok else 0.35,
            source="source_config_manager",
            metadata=asdict(result),
        )


def read_yaml_mapping(path: Path) -> dict[str, Any]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError(f"expected mapping in {path}")
    return raw


def backup_config(path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(path.suffix + f".{timestamp}.bak")
    shutil.copy2(path, backup)
    return backup


def set_nested(raw: dict[str, Any], parts: list[str], value: Any) -> None:
    cursor = raw
    for part in parts[:-1]:
        child = cursor.get(part)
        if not isinstance(child, dict):
            child = {}
            cursor[part] = child
        cursor = child
    cursor[parts[-1]] = value


def restart_launchd(label: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os_uid()}/{label}"], text=True, capture_output=True, timeout=60)


def os_uid() -> int:
    import os

    return os.getuid()

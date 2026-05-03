from __future__ import annotations

from pathlib import Path
from typing import Any

from robin.core.config import VNextConfig, load_vnext_config


def is_vnext_enabled(raw_config: Any) -> bool:
    """Return whether legacy callers should delegate to vNext.

    The compatibility default is conservative: if legacy config does not
    mention vNext, old behavior remains active. This makes rollback a config
    change instead of an import-path change.
    """

    if raw_config is None:
        return False
    if isinstance(raw_config, dict):
        section = raw_config.get("vnext", {})
        return bool(section.get("enabled", False))
    section = getattr(raw_config, "vnext", None)
    if isinstance(section, dict):
        return bool(section.get("enabled", False))
    return bool(getattr(section, "enabled", False)) if section is not None else False


def vnext_config_from_legacy(raw_config: Any, *, root: Path | None = None) -> VNextConfig:
    """Build a vNext config from legacy config without copying secrets."""

    payload: dict[str, Any] = {}
    if isinstance(raw_config, dict):
        payload.update(raw_config.get("vnext", {}))
    section = getattr(raw_config, "vnext", None)
    if isinstance(section, dict):
        payload.update(section)
    if root is not None:
        payload["root"] = root
    return load_vnext_config(payload or None, root=root)


def compat_status(raw_config: Any) -> dict[str, object]:
    return {
        "vnext_enabled": is_vnext_enabled(raw_config),
        "fallback": "legacy portfolio_bot implementation",
        "live_trading": "hard-disabled unless explicitly configured outside vNext",
    }

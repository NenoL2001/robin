from __future__ import annotations

from typer.testing import CliRunner

import main
import portfolio_bot.data_hub
import portfolio_bot.monitor
import portfolio_bot.research
import portfolio_bot.workers
from robin.app.cli import app
from robin.app.compat import compat_status, is_vnext_enabled


def test_legacy_import_paths_still_exist() -> None:
    assert callable(main.main)
    assert portfolio_bot.research
    assert portfolio_bot.monitor
    assert portfolio_bot.workers
    assert portfolio_bot.data_hub


def test_compat_facade_defaults_to_legacy() -> None:
    assert not is_vnext_enabled({})
    assert is_vnext_enabled({"vnext": {"enabled": True}})
    assert compat_status({})["fallback"] == "legacy portfolio_bot implementation"


def test_robin_vnext_cli_dry_run() -> None:
    result = CliRunner().invoke(app, ["ingest", "run", "--dry-run"])

    assert result.exit_code == 0
    assert "lake_root" in result.output

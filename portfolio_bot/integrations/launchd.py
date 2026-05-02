from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def render_plist(config_path: Path, workdir: Path, label: str = "com.noah.portfolio-bot") -> str:
    template_path = workdir / "launchd" / "com.noah.portfolio-bot.plist.template"
    if template_path.exists():
        template = template_path.read_text(encoding="utf-8")
    else:
        template = DEFAULT_TEMPLATE
    logdir = Path.home() / "Library" / "Logs"
    python = sys.executable
    path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return (
        template.replace("com.noah.portfolio-bot", label)
        .replace("{python}", python)
        .replace("{config}", str(config_path))
        .replace("{workdir}", str(workdir))
        .replace("{logdir}", str(logdir))
        .replace("{path}", path)
    )


def install_launchd(config_path: Path, workdir: Path, label: str = "com.noah.portfolio-bot") -> Path:
    plist = render_plist(config_path.resolve(), workdir.resolve(), label=label)
    target_dir = Path.home() / "Library" / "LaunchAgents"
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{label}.plist"
    target.write_text(plist, encoding="utf-8")
    launchctl = shutil.which("launchctl")
    if launchctl:
        subprocess.run([launchctl, "unload", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        subprocess.run([launchctl, "load", str(target)], check=False)
    return target


DEFAULT_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.noah.portfolio-bot</string>
  <key>ProgramArguments</key>
  <array><string>{python}</string><string>-m</string><string>portfolio_bot</string><string>run</string><string>--config</string><string>{config}</string></array>
  <key>WorkingDirectory</key><string>{workdir}</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>{logdir}/portfolio-bot.out.log</string>
  <key>StandardErrorPath</key><string>{logdir}/portfolio-bot.err.log</string>
</dict>
</plist>
"""

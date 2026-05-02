from __future__ import annotations

import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import BotConfig
from ..notifications import build_notifier
from .observability import expected_worker_counts, setup_logging
from ..runtime import RuntimeStore, runtime_path


@dataclass(slots=True)
class WorkerProcess:
    role: str
    index: int
    process: subprocess.Popen
    restarts: int = 0
    last_start: float = 0.0


class Supervisor:
    def __init__(self, config: BotConfig, config_path: Path, *, dry_run: bool = False):
        self.config = config
        self.config_path = config_path.resolve()
        self.dry_run = dry_run
        self.runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.notifier = build_notifier(config, dry_run=dry_run)
        self.logger = setup_logging(config, "supervisor", "supervisor")
        self.processes: dict[tuple[str, int], WorkerProcess] = {}

    def run_forever(self) -> None:
        self.runtime.record_log("INFO", "supervisor", "supervisor", "supervisor started", {"dry_run": self.dry_run})
        self.start_all()
        try:
            while True:
                self.runtime.heartbeat("supervisor", "supervisor", os.getpid())
                self.check_processes()
                time.sleep(5)
        finally:
            self.stop_all()
            self.runtime.record_log("INFO", "supervisor", "supervisor", "supervisor stopped", {})

    def start_all(self) -> None:
        for role, count in worker_counts(self.config).items():
            for index in range(count):
                self.start_worker(role, index)

    def check_processes(self) -> None:
        for key, worker in list(self.processes.items()):
            if worker.process.poll() is None:
                continue
            role, index = key
            code = worker.process.returncode
            worker.restarts += 1
            self.logger.warning("worker exited", extra={"role": role, "worker_id": f"{role}[{index}]", "status": str(code)})
            self.runtime.record_log("WARNING", "supervisor", "supervisor", "worker exited", {"role": role, "index": index, "code": code, "restarts": worker.restarts})
            if worker.restarts >= 3:
                self.notifier.send("Portfolio bot worker restart", f"{role}[{index}] exited with {code}; restarting, count={worker.restarts}")
            time.sleep(self.config.workers.restart_backoff_seconds)
            self.start_worker(role, index, restarts=worker.restarts)

    def start_worker(self, role: str, index: int, restarts: int = 0) -> None:
        cmd = [
            sys.executable,
            "-m",
            "portfolio_bot",
            "--config",
            str(self.config_path),
            "worker",
            role,
        ]
        if self.dry_run:
            cmd.append("--dry-run")
        process = subprocess.Popen(cmd, cwd=str(self.config.root))
        self.processes[(role, index)] = WorkerProcess(role=role, index=index, process=process, restarts=restarts, last_start=time.time())
        self.runtime.record_log("INFO", "supervisor", "supervisor", "worker started", {"role": role, "index": index, "pid": process.pid, "restarts": restarts})

    def stop_all(self) -> None:
        for worker in self.processes.values():
            if worker.process.poll() is None:
                worker.process.terminate()
        for worker in self.processes.values():
            try:
                worker.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.process.kill()


def worker_counts(config: BotConfig) -> dict[str, int]:
    return expected_worker_counts(config)

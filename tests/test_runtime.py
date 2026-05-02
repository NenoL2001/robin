from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

from portfolio_bot.config import load_config
from portfolio_bot.observability import run_health_check
from portfolio_bot.runtime import JOB_DONE, RuntimeStore


def test_job_claim_is_atomic_across_workers(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.enqueue_job("major_event_analysis", {"symbol": "POET"}, idempotency_key="event-1")

    def claim(worker):
        local = RuntimeStore(tmp_path / "runtime.sqlite")
        return local.claim_job(worker, ["major_event_analysis"])

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(claim, [f"worker-{idx}" for idx in range(4)]))

    claimed = [result for result in results if result is not None]
    assert len(claimed) == 1
    assert claimed[0].locked_by.startswith("worker-")


def test_seen_news_and_cooldown_are_persistent(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")

    assert store.filter_fresh_news_keys({"a", "b"}) == {"a", "b"}
    assert store.filter_fresh_news_keys({"a", "b", "c"}) == {"c"}

    assert store.check_and_touch_cooldown("POET:move", timedelta(minutes=30))
    assert not store.check_and_touch_cooldown("POET:move", timedelta(minutes=30))
    assert store.check_and_touch_cooldown("POET:move", timedelta(minutes=30), commit=False) is False


def test_job_complete_updates_status(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")
    store.enqueue_job("backtest", {"prices": [1, 2, 3]}, idempotency_key="bt-1")
    job = store.claim_job("worker", ["backtest"])
    store.complete_job(job.id, {"ok": True})

    jobs = store.jobs(status=JOB_DONE)
    assert len(jobs) == 1
    assert jobs[0].result == {"ok": True}


def test_legacy_state_and_seen_news_migrate(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "state.json").write_text(
        json.dumps(
            {
                "quotes": {"POET": {"symbol": "POET", "price": 10, "timestamp": datetime.now(timezone.utc).isoformat()}},
                "cooldowns": {"POET:intraday_move": datetime.now(timezone.utc).isoformat()},
            }
        ),
        encoding="utf-8",
    )
    (data_dir / "seen_news.json").write_text(json.dumps(["news-1"]), encoding="utf-8")

    store = RuntimeStore(data_dir / "runtime.sqlite")

    assert store.quote_snapshot("POET").price == 10
    assert not store.check_and_touch_cooldown("POET:intraday_move", timedelta(days=1))
    assert store.filter_fresh_news_keys({"news-1", "news-2"}) == {"news-2"}


def test_runtime_logs_profiles_and_health_are_persistent(tmp_path):
    store = RuntimeStore(tmp_path / "runtime.sqlite")

    store.record_log("INFO", "health", "worker-1", "ok", {"a": 1})
    store.record_profile("scan_once", "profile", "worker-1", ok=True, duration_ms=12.5, cpu_ms=3.0, peak_kb=128.0)
    store.record_health("ok", "系统健康：正常", {"running_by_role": {"health": 1}})

    assert store.recent_logs(limit=1)[0]["fields"] == {"a": 1}
    summary = store.profile_summary(limit=10)
    assert summary[0]["operation"] == "scan_once"
    assert summary[0]["avg_ms"] == 12.5
    assert store.recent_health(limit=1)[0]["details"]["running_by_role"]["health"] == 1
    status = store.status()
    assert status["logs"] == 1
    assert status["profile_runs"] == 1
    assert status["health_checks"] == 1


def test_health_counts_alive_stale_worker_as_running(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
workers:
  orchestrator_processes: 0
  realtime_processes: 0
  news_processes: 1
  ai_processes: 0
  report_processes: 0
  agent_processes: 0
  strategy_processes: 0
  paper_processes: 0
  backtest_processes: 0
  health_processes: 0
  maintenance_processes: 0
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    store = RuntimeStore(config.data_dir / config.runtime.sqlite_path)
    store.heartbeat("news-worker", "news", 12345, status="stale")
    monkeypatch.setattr("portfolio_bot.agents.observability.pid_alive", lambda pid: pid == 12345)

    result = run_health_check(config, "test-health", dry_run=True)

    assert result["status"] == "ok"
    assert result["details"]["running_by_role"]["news"] == 1
    assert result["details"]["stale_by_role"]["news"] == 1


def test_health_ignores_dead_old_generation_when_current_worker_alive(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
monitor:
  deep_scan_seconds: 300
workers:
  orchestrator_processes: 0
  realtime_processes: 0
  news_processes: 1
  ai_processes: 0
  report_processes: 0
  agent_processes: 0
  strategy_processes: 0
  paper_processes: 0
  backtest_processes: 0
  health_processes: 0
  maintenance_processes: 0
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(config_path)
    store = RuntimeStore(config.data_dir / config.runtime.sqlite_path)
    store.heartbeat("old-news", "news", 111, status="running")
    store.heartbeat("new-news", "news", 222, status="running")
    monkeypatch.setattr("portfolio_bot.agents.observability.pid_alive", lambda pid: pid == 222)

    result = run_health_check(config, "test-health", dry_run=True)

    assert result["status"] == "ok"
    assert result["details"]["running_by_role"]["news"] == 1
    assert result["details"]["missing"] == []
    assert result["details"]["dead_pids"] == []
    assert result["details"]["stale_after_seconds"] >= 300

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import MarketEvent, Quote
from .storage import quote_to_snapshot, snapshot_to_quote


JOB_PENDING = "pending"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"


@dataclass(slots=True)
class JobRecord:
    id: int
    type: str
    status: str
    priority: int
    run_after: str
    locked_by: str
    locked_at: str
    attempts: int
    payload: dict[str, Any]
    result: dict[str, Any]
    error: str
    idempotency_key: str
    created_at: str
    updated_at: str


class RuntimeStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._migrate_legacy_files()

    def enqueue_job(
        self,
        job_type: str,
        payload: dict[str, Any],
        *,
        priority: int = 0,
        run_after: datetime | None = None,
        idempotency_key: str | None = None,
    ) -> int | None:
        now = utc_now()
        key = idempotency_key or f"{job_type}:{uuid.uuid4()}"
        run_at = (run_after or datetime.now(timezone.utc)).isoformat()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO jobs
                  (type, status, priority, run_after, attempts, payload_json, result_json,
                   error, idempotency_key, created_at, updated_at)
                VALUES (?, ?, ?, ?, 0, ?, '{}', '', ?, ?, ?)
                """,
                (job_type, JOB_PENDING, int(priority), run_at, dumps(payload), key, now, now),
            )
            return int(cursor.lastrowid) if cursor.rowcount else None

    def claim_job(self, worker_id: str, job_types: Iterable[str], stale_after_seconds: int = 600) -> JobRecord | None:
        job_types = list(job_types)
        if not job_types:
            return None
        placeholders = ",".join("?" for _ in job_types)
        now_dt = datetime.now(timezone.utc)
        now = now_dt.isoformat()
        stale_before = (now_dt - timedelta(seconds=stale_after_seconds)).isoformat()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, locked_by = '', locked_at = '', updated_at = ?
                WHERE status = ? AND locked_at < ?
                """,
                (JOB_PENDING, now, JOB_RUNNING, stale_before),
            )
            row = conn.execute(
                f"""
                SELECT * FROM jobs
                WHERE status = ? AND run_after <= ? AND type IN ({placeholders})
                ORDER BY priority DESC, created_at ASC
                LIMIT 1
                """,
                [JOB_PENDING, now, *job_types],
            ).fetchone()
            if not row:
                return None
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, locked_by = ?, locked_at = ?, attempts = attempts + 1, updated_at = ?
                WHERE id = ?
                """,
                (JOB_RUNNING, worker_id, now, now, int(row["id"])),
            )
            updated = conn.execute("SELECT * FROM jobs WHERE id = ?", (int(row["id"]),)).fetchone()
        return job_from_row(updated) if updated else None

    def complete_job(self, job_id: int, result: dict[str, Any] | None = None) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, locked_by = '', locked_at = '', result_json = ?, error = '', updated_at = ?
                WHERE id = ?
                """,
                (JOB_DONE, dumps(result or {}), now, int(job_id)),
            )

    def fail_job(self, job_id: int, error: str, *, retry: bool = False, delay_seconds: int = 60) -> None:
        now_dt = datetime.now(timezone.utc)
        status = JOB_PENDING if retry else JOB_FAILED
        run_after = (now_dt + timedelta(seconds=delay_seconds)).isoformat() if retry else now_dt.isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, run_after = ?, locked_by = '', locked_at = '', error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, run_after, sanitize_secrets(error)[:2000], now_dt.isoformat(), int(job_id)),
            )

    def jobs(self, status: str = "", limit: int = 50) -> list[JobRecord]:
        with self._connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, int(limit)),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [job_from_row(row) for row in rows]

    def save_quote_snapshot(self, quote: Quote) -> None:
        now = utc_now()
        payload = quote_to_snapshot(quote)
        with self._connect() as conn:
            old = conn.execute("SELECT quote_json FROM quote_snapshots WHERE symbol = ?", (quote.symbol.upper(),)).fetchone()
            previous_json = old["quote_json"] if old else ""
            conn.execute(
                """
                INSERT INTO quote_snapshots(symbol, quote_json, previous_quote_json, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                  previous_quote_json = quote_snapshots.quote_json,
                  quote_json = excluded.quote_json,
                  updated_at = excluded.updated_at
                """,
                (quote.symbol.upper(), dumps(payload), previous_json, now),
            )

    def quote_snapshot(self, symbol: str) -> Quote | None:
        with self._connect() as conn:
            row = conn.execute("SELECT quote_json FROM quote_snapshots WHERE symbol = ?", (symbol.upper(),)).fetchone()
        if not row:
            return None
        return snapshot_to_quote(json.loads(row["quote_json"]))

    def previous_quote_snapshot(self, symbol: str) -> Quote | None:
        with self._connect() as conn:
            row = conn.execute("SELECT previous_quote_json FROM quote_snapshots WHERE symbol = ?", (symbol.upper(),)).fetchone()
        if not row or not row["previous_quote_json"]:
            return None
        return snapshot_to_quote(json.loads(row["previous_quote_json"]))

    def check_and_touch_cooldown(self, key: str, cooldown: timedelta, *, commit: bool = True) -> bool:
        now = datetime.now(timezone.utc)
        with self._connect() as conn:
            row = conn.execute("SELECT last_sent_at FROM cooldowns WHERE key = ?", (key,)).fetchone()
            if row and now - datetime.fromisoformat(row["last_sent_at"]) < cooldown:
                return False
            if commit:
                conn.execute(
                    """
                    INSERT INTO cooldowns(key, last_sent_at, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET last_sent_at = excluded.last_sent_at, updated_at = excluded.updated_at
                    """,
                    (key, now.isoformat(), now.isoformat()),
                )
        return True

    def check_and_touch_repeat_limit(self, key: str, window: timedelta, max_count: int, *, commit: bool = True) -> bool:
        if max_count <= 0:
            return False
        now = datetime.now(timezone.utc)
        slot_to_touch = ""
        with self._connect() as conn:
            if commit:
                conn.execute("BEGIN IMMEDIATE")
            for index in range(max_count):
                slot_key = f"repeat:{key}:{index + 1}"
                row = conn.execute("SELECT last_sent_at FROM cooldowns WHERE key = ?", (slot_key,)).fetchone()
                if not row or now - datetime.fromisoformat(row["last_sent_at"]) >= window:
                    slot_to_touch = slot_key
                    break
            if not slot_to_touch:
                return False
            if commit:
                conn.execute(
                    """
                    INSERT INTO cooldowns(key, last_sent_at, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET last_sent_at = excluded.last_sent_at, updated_at = excluded.updated_at
                    """,
                    (slot_to_touch, now.isoformat(), now.isoformat()),
                )
        return True

    def filter_fresh_news_keys(self, keys: Iterable[str], *, commit: bool = True) -> set[str]:
        keys = {key for key in keys if key}
        if not keys:
            return set()
        fresh: set[str] = set()
        now = utc_now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for key in keys:
                row = conn.execute("SELECT key FROM seen_news WHERE key = ?", (key,)).fetchone()
                if row:
                    continue
                fresh.add(key)
                if commit:
                    conn.execute("INSERT INTO seen_news(key, first_seen_at) VALUES (?, ?)", (key, now))
        return fresh

    def add_market_event(self, event: MarketEvent, *, idempotency_key: str) -> int | None:
        now = utc_now()
        payload = market_event_payload(event)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO market_events
                  (idempotency_key, symbol, event_type, severity, message, payload_json, ai_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    idempotency_key,
                    event.symbol.upper(),
                    event.event_type,
                    event.severity,
                    event.message,
                    dumps(payload),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid) if cursor.rowcount else None

    def update_market_event_ai_status(self, idempotency_key: str, status: str) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE market_events SET ai_status = ?, updated_at = ? WHERE idempotency_key = ?",
                (status, now, idempotency_key),
            )

    def heartbeat(self, worker_id: str, role: str, pid: int, status: str = "running") -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO worker_heartbeats(worker_id, role, pid, status, last_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(worker_id) DO UPDATE SET
                  role = excluded.role, pid = excluded.pid, status = excluded.status,
                  last_seen_at = excluded.last_seen_at, updated_at = excluded.updated_at
                """,
                (worker_id, role, int(pid), status, now, now),
            )

    def record_log(self, level: str, role: str, worker_id: str, message: str, fields: dict[str, Any] | None = None) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO runtime_logs(level, role, worker_id, message, fields_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (level.upper(), role, worker_id, sanitize_secrets(message)[:1000], dumps(sanitize_mapping(fields or {})), now),
            )
            return int(cursor.lastrowid)

    def recent_logs(self, level: str = "", role: str = "", limit: int = 50) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if level:
            clauses.append("level = ?")
            params.append(level.upper())
        if role:
            clauses.append("role = ?")
            params.append(role)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM runtime_logs {where} ORDER BY created_at DESC LIMIT ?",
                [*params, int(limit)],
            ).fetchall()
        return [decode_json_columns(dict(row), "fields_json") for row in rows]

    def record_profile(
        self,
        operation: str,
        role: str,
        worker_id: str,
        *,
        ok: bool,
        duration_ms: float,
        cpu_ms: float,
        peak_kb: float = 0.0,
        metadata: dict[str, Any] | None = None,
        error: str = "",
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO profile_runs
                  (operation, role, worker_id, ok, duration_ms, cpu_ms, peak_kb, metadata_json, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    operation,
                    role,
                    worker_id,
                    1 if ok else 0,
                    float(duration_ms),
                    float(cpu_ms),
                    float(peak_kb),
                    dumps(metadata or {}),
                    sanitize_secrets(error)[:2000],
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def recent_profiles(self, operation: str = "", role: str = "", limit: int = 50) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []
        if operation:
            clauses.append("operation = ?")
            params.append(operation)
        if role:
            clauses.append("role = ?")
            params.append(role)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM profile_runs {where} ORDER BY created_at DESC LIMIT ?",
                [*params, int(limit)],
            ).fetchall()
        return [decode_json_columns(dict(row), "metadata_json") for row in rows]

    def profile_summary(self, limit: int = 200) -> list[dict[str, Any]]:
        rows = self.recent_profiles(limit=limit)
        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            groups.setdefault(str(row["operation"]), []).append(row)
        summary = []
        for operation, items in sorted(groups.items()):
            durations = [float(item["duration_ms"]) for item in items]
            cpu_times = [float(item["cpu_ms"]) for item in items]
            errors = [str(item["error"]) for item in items if item["error"]]
            ok_count = sum(1 for item in items if int(item["ok"]) == 1)
            summary.append(
                {
                    "operation": operation,
                    "runs": len(items),
                    "ok": ok_count,
                    "failed": len(items) - ok_count,
                    "avg_ms": round(sum(durations) / len(durations), 2),
                    "max_ms": round(max(durations), 2),
                    "avg_cpu_ms": round(sum(cpu_times) / len(cpu_times), 2),
                    "max_peak_kb": round(max(float(item["peak_kb"]) for item in items), 2),
                    "last_error": errors[0] if errors else "",
                }
            )
        return summary

    def record_health(self, status: str, summary: str, details: dict[str, Any]) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO health_checks(status, summary, details_json, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (status, summary[:1000], dumps(details), now),
            )
            return int(cursor.lastrowid)

    def recent_health(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM health_checks ORDER BY created_at DESC LIMIT ?", (int(limit),)).fetchall()
        return [decode_json_columns(dict(row), "details_json") for row in rows]

    def stale_running_jobs(self, stale_after_seconds: int = 900, limit: int = 20) -> list[JobRecord]:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM jobs
                WHERE status = ? AND locked_at < ?
                ORDER BY locked_at ASC
                LIMIT ?
                """,
                (JOB_RUNNING, cutoff, int(limit)),
            ).fetchall()
        return [job_from_row(row) for row in rows]

    def quote_snapshot_status(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT symbol, updated_at FROM quote_snapshots ORDER BY updated_at DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [dict(row) for row in rows]

    def upsert_news_item(
        self,
        dedupe_key: str,
        *,
        title: str,
        url: str,
        source: str,
        symbols: list[str],
        summary: str = "",
        kind: str = "news",
        published_at: str = "",
        raw: dict[str, Any] | None = None,
    ) -> None:
        now = utc_now()
        symbols_text = ",".join(sorted({symbol.upper() for symbol in symbols if symbol}))
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO news_items
                  (dedupe_key, title, url, source, symbols_text, summary, kind, published_at, raw_json, first_seen_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(dedupe_key) DO UPDATE SET
                  title = excluded.title,
                  url = excluded.url,
                  source = excluded.source,
                  symbols_text = excluded.symbols_text,
                  summary = excluded.summary,
                  kind = excluded.kind,
                  published_at = excluded.published_at,
                  raw_json = excluded.raw_json,
                  updated_at = excluded.updated_at
                """,
                (
                    dedupe_key,
                    title[:1000],
                    url,
                    source,
                    symbols_text,
                    summary[:4000],
                    kind,
                    published_at,
                    dumps(raw or {}),
                    now,
                    now,
                ),
            )

    def touch_news_cache(self, symbols: Iterable[str], *, source: str = "news") -> None:
        symbol_set = {symbol.upper() for symbol in symbols if symbol}
        if not symbol_set:
            return
        now = utc_now()
        with self._connect() as conn:
            for symbol in symbol_set:
                conn.execute(
                    """
                    INSERT INTO news_cache(symbol, source, refreshed_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(symbol, source) DO UPDATE SET
                      refreshed_at = excluded.refreshed_at,
                      updated_at = excluded.updated_at
                    """,
                    (symbol, source, now, now),
                )

    def recent_news_items(self, symbols: Iterable[str], *, since: datetime | None = None, limit: int = 500) -> list[dict[str, Any]]:
        symbol_set = {symbol.upper() for symbol in symbols if symbol}
        if not symbol_set:
            return []
        clauses = []
        params: list[Any] = []
        for symbol in symbol_set:
            clauses.append("(symbols_text = ? OR symbols_text LIKE ? OR symbols_text LIKE ? OR symbols_text LIKE ?)")
            params.extend([symbol, f"{symbol},%", f"%,{symbol},%", f"%,{symbol}"])
        where = f"({' OR '.join(clauses)})"
        if since:
            where += " AND COALESCE(NULLIF(published_at, ''), first_seen_at) >= ?"
            params.append(since.isoformat())
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM news_items WHERE {where} ORDER BY COALESCE(NULLIF(published_at, ''), first_seen_at) DESC LIMIT ?",
                [*params, int(limit)],
            ).fetchall()
        return [decode_json_columns(dict(row), "raw_json") for row in rows]

    def news_cache_age_seconds(self, symbol: str) -> float | None:
        symbol = symbol.upper()
        with self._connect() as conn:
            cache_row = conn.execute(
                "SELECT MAX(refreshed_at) AS refreshed_at FROM news_cache WHERE symbol = ?",
                (symbol,),
            ).fetchone()
            if cache_row and cache_row["refreshed_at"]:
                try:
                    return max(0.0, datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(cache_row["refreshed_at"]).timestamp())
                except ValueError:
                    pass
            row = conn.execute(
                """
                SELECT MAX(updated_at) AS updated_at
                FROM news_items
                WHERE symbols_text = ? OR symbols_text LIKE ? OR symbols_text LIKE ? OR symbols_text LIKE ?
                """,
                (symbol, f"{symbol},%", f"%,{symbol},%", f"%,{symbol}"),
            ).fetchone()
        if not row or not row["updated_at"]:
            return None
        try:
            return max(0.0, datetime.now(timezone.utc).timestamp() - datetime.fromisoformat(row["updated_at"]).timestamp())
        except ValueError:
            return None

    def save_feature_snapshot(self, symbol: str, features: dict[str, Any], *, source: str = "feature_engine") -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO feature_snapshots(symbol, features_json, source, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (symbol.upper(), dumps(features), source, now),
            )
            row_id = int(cursor.lastrowid)
        self.save_metric_snapshot(
            symbol,
            "feature_snapshot",
            features,
            window=str(features.get("window", "latest")),
            source=source,
            as_of=features.get("computed_at") or now,
        )
        return row_id

    def latest_features(self, symbol: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT features_json FROM feature_snapshots WHERE symbol = ? ORDER BY created_at DESC LIMIT 1",
                (symbol.upper(),),
            ).fetchone()
        if not row:
            return {}
        try:
            return json.loads(row["features_json"] or "{}")
        except json.JSONDecodeError:
            return {}

    def save_metric_snapshot(
        self,
        symbol: str,
        metric_name: str,
        value: dict[str, Any],
        *,
        window: str = "latest",
        source: str = "metric_service",
        as_of: str | datetime | None = None,
    ) -> int:
        now = utc_now()
        if isinstance(as_of, datetime):
            as_of_text = as_of.isoformat()
        else:
            as_of_text = str(as_of or now)
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO metric_snapshots(symbol, metric_name, window, source, as_of, value_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (symbol.upper(), metric_name, window, source, as_of_text, dumps(value), now),
            )
            return int(cursor.lastrowid)

    def latest_metric_snapshot(self, symbol: str, metric_name: str = "", *, window: str = "") -> dict[str, Any]:
        clauses = ["symbol = ?"]
        params: list[Any] = [symbol.upper()]
        if metric_name:
            clauses.append("metric_name = ?")
            params.append(metric_name)
        if window:
            clauses.append("window = ?")
            params.append(window)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT * FROM metric_snapshots WHERE {' AND '.join(clauses)} ORDER BY as_of DESC, created_at DESC LIMIT 1",
                params,
            ).fetchone()
        if not row:
            return {}
        return decode_json_columns(dict(row), "value_json")

    def save_real_position_snapshot(self, positions: list[dict[str, Any]], *, source: str = "holdings.yaml") -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO real_position_snapshots(source, positions_json, created_at)
                VALUES (?, ?, ?)
                """,
                (source, dumps({"positions": positions}), now),
            )
            return int(cursor.lastrowid)

    def latest_real_position_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM real_position_snapshots ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return {}
        payload = decode_json_columns(dict(row), "positions_json")
        return payload

    def save_strategy_state(
        self,
        strategy_name: str,
        strategy_version: str,
        status: str,
        state: dict[str, Any],
        *,
        config_hash: str = "",
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO strategy_states(strategy_name, strategy_version, status, config_hash, state_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (strategy_name, strategy_version, status, config_hash, dumps(state), now),
            )
            return int(cursor.lastrowid)

    def latest_strategy_state(self, strategy_name: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM strategy_states WHERE strategy_name = ? ORDER BY created_at DESC LIMIT 1",
                (strategy_name,),
            ).fetchone()
        if not row:
            return {}
        return decode_json_columns(dict(row), "state_json")

    def record_strategy_change(
        self,
        strategy_name: str,
        strategy_version: str,
        change_type: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO strategy_changes(strategy_name, strategy_version, change_type, summary, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (strategy_name, strategy_version, change_type, summary[:2000], dumps(metadata or {}), now),
            )
            return int(cursor.lastrowid)

    def recent_strategy_changes(self, strategy_name: str = "", limit: int = 20) -> list[dict[str, Any]]:
        if strategy_name:
            query = "SELECT * FROM strategy_changes WHERE strategy_name = ? ORDER BY created_at DESC LIMIT ?"
            params: list[Any] = [strategy_name, int(limit)]
        else:
            query = "SELECT * FROM strategy_changes ORDER BY created_at DESC LIMIT ?"
            params = [int(limit)]
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [decode_json_columns(dict(row), "metadata_json") for row in rows]

    def worker_statuses(self, stale_after_seconds: int = 120) -> list[dict[str, Any]]:
        self.mark_stale_workers(stale_after_seconds)
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM worker_heartbeats ORDER BY role, worker_id").fetchall()
        return [dict(row) for row in rows]

    def mark_stale_workers(self, stale_after_seconds: int = 120) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=stale_after_seconds)).isoformat()
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE worker_heartbeats SET status = 'stale', updated_at = ? WHERE status = 'running' AND last_seen_at < ?",
                (now, cutoff),
            )

    def mark_workers_status(self, worker_ids: Iterable[str], status: str) -> None:
        worker_ids = [worker_id for worker_id in worker_ids if worker_id]
        if not worker_ids:
            return
        now = utc_now()
        placeholders = ",".join("?" for _ in worker_ids)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE worker_heartbeats SET status = ?, updated_at = ? WHERE worker_id IN ({placeholders})",
                [status, now, *worker_ids],
            )

    def start_agent_run(self, agent_name: str, role: str, objective: str, metadata: dict[str, Any] | None = None) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_runs(agent_name, role, objective, status, metadata_json, result_json, error, created_at, updated_at)
                VALUES (?, ?, ?, 'running', ?, '{}', '', ?, ?)
                """,
                (agent_name, role, objective[:2000], dumps(metadata or {}), now, now),
            )
            return int(cursor.lastrowid)

    def update_agent_run(
        self,
        run_id: int,
        *,
        status: str,
        result: dict[str, Any] | None = None,
        error: str = "",
    ) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_runs
                SET status = ?, result_json = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, dumps(result or {}), sanitize_secrets(error)[:2000], now, int(run_id)),
            )

    def add_agent_step(
        self,
        run_id: int,
        step_name: str,
        status: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_steps(run_id, step_name, status, summary, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (int(run_id), step_name, status, summary[:2000], dumps(sanitize_mapping(metadata or {})), now),
            )
            return int(cursor.lastrowid)

    def recent_agent_runs(self, agent_name: str = "", limit: int = 20) -> list[dict[str, Any]]:
        if agent_name:
            query = "SELECT * FROM agent_runs WHERE agent_name = ? ORDER BY created_at DESC LIMIT ?"
            params: list[Any] = [agent_name, int(limit)]
        else:
            query = "SELECT * FROM agent_runs ORDER BY created_at DESC LIMIT ?"
            params = [int(limit)]
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [decode_json_columns(dict(row), "metadata_json", "result_json") for row in rows]

    def agent_steps(self, run_id: int) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM agent_steps WHERE run_id = ? ORDER BY id", (int(run_id),)).fetchall()
        return [decode_json_columns(dict(row), "metadata_json") for row in rows]

    def add_agent_task(
        self,
        run_id: int,
        task_id: str,
        name: str,
        description: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_tasks(run_id, task_id, name, description, status, metadata_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    task_id[:120],
                    name[:200],
                    sanitize_secrets(description)[:2000],
                    status,
                    dumps(sanitize_mapping(metadata or {})),
                    now,
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def update_agent_task(self, run_id: int, task_id: str, status: str, metadata: dict[str, Any] | None = None) -> None:
        now = utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE agent_tasks
                SET status = ?, metadata_json = ?, updated_at = ?
                WHERE run_id = ? AND task_id = ?
                """,
                (status, dumps(sanitize_mapping(metadata or {})), now, int(run_id), task_id),
            )

    def add_agent_tool_call(
        self,
        run_id: int,
        tool_name: str,
        status: str,
        input_payload: dict[str, Any] | None = None,
        output_payload: dict[str, Any] | None = None,
        *,
        error: str = "",
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_tool_calls(run_id, tool_name, status, input_json, output_json, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    tool_name[:160],
                    status,
                    dumps(sanitize_mapping(input_payload or {})),
                    dumps(sanitize_mapping(output_payload or {})),
                    sanitize_secrets(error)[:2000],
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def add_agent_trace_span(
        self,
        run_id: int,
        span_name: str,
        status: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_trace_spans(run_id, span_name, status, summary, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    span_name[:160],
                    status,
                    sanitize_secrets(summary)[:2000],
                    dumps(sanitize_mapping(metadata or {})),
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def add_agent_memory_link(
        self,
        run_id: int,
        memory_id: int | None,
        layer: str,
        reason: str,
        confidence: float,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_memory_links(run_id, memory_id, layer, reason, confidence, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    int(memory_id) if memory_id is not None else None,
                    layer[:80],
                    sanitize_secrets(reason)[:1000],
                    float(confidence),
                    dumps(sanitize_mapping(metadata or {})),
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def add_agent_reflection(
        self,
        run_id: int,
        kind: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_reflections(run_id, kind, content, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    kind[:80],
                    sanitize_secrets(content)[:4000],
                    dumps(sanitize_mapping(metadata or {})),
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def add_agent_artifact(
        self,
        run_id: int,
        artifact_type: str,
        path: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        now = utc_now()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO agent_artifacts(run_id, artifact_type, path, summary, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(run_id),
                    artifact_type[:80],
                    sanitize_secrets(path)[:1000],
                    sanitize_secrets(summary)[:2000],
                    dumps(sanitize_mapping(metadata or {})),
                    now,
                ),
            )
            return int(cursor.lastrowid)

    def agent_trace(self, run_id: int) -> dict[str, Any]:
        with self._connect() as conn:
            run = conn.execute("SELECT * FROM agent_runs WHERE id = ?", (int(run_id),)).fetchone()
            if not run:
                return {}
            steps = conn.execute("SELECT * FROM agent_steps WHERE run_id = ? ORDER BY id", (int(run_id),)).fetchall()
            tasks = conn.execute("SELECT * FROM agent_tasks WHERE run_id = ? ORDER BY id", (int(run_id),)).fetchall()
            tool_calls = conn.execute("SELECT * FROM agent_tool_calls WHERE run_id = ? ORDER BY id", (int(run_id),)).fetchall()
            spans = conn.execute("SELECT * FROM agent_trace_spans WHERE run_id = ? ORDER BY id", (int(run_id),)).fetchall()
            memory_links = conn.execute("SELECT * FROM agent_memory_links WHERE run_id = ? ORDER BY id", (int(run_id),)).fetchall()
            reflections = conn.execute("SELECT * FROM agent_reflections WHERE run_id = ? ORDER BY id", (int(run_id),)).fetchall()
            artifacts = conn.execute("SELECT * FROM agent_artifacts WHERE run_id = ? ORDER BY id", (int(run_id),)).fetchall()
        return {
            "run": decode_json_columns(dict(run), "metadata_json", "result_json"),
            "steps": [decode_json_columns(dict(row), "metadata_json") for row in steps],
            "tasks": [decode_json_columns(dict(row), "metadata_json") for row in tasks],
            "tool_calls": [decode_json_columns(dict(row), "input_json", "output_json") for row in tool_calls],
            "trace_spans": [decode_json_columns(dict(row), "metadata_json") for row in spans],
            "memory_links": [decode_json_columns(dict(row), "metadata_json") for row in memory_links],
            "reflections": [decode_json_columns(dict(row), "metadata_json") for row in reflections],
            "artifacts": [decode_json_columns(dict(row), "metadata_json") for row in artifacts],
        }

    def status(self) -> dict[str, Any]:
        with self._connect() as conn:
            job_counts = {
                row["status"]: int(row["count"])
                for row in conn.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
            }
            return {
                "path": str(self.path),
                "jobs": job_counts,
                "quote_snapshots": int(conn.execute("SELECT COUNT(*) FROM quote_snapshots").fetchone()[0]),
                "seen_news": int(conn.execute("SELECT COUNT(*) FROM seen_news").fetchone()[0]),
                "market_events": int(conn.execute("SELECT COUNT(*) FROM market_events").fetchone()[0]),
                "workers": int(conn.execute("SELECT COUNT(*) FROM worker_heartbeats").fetchone()[0]),
                "profile_runs": int(conn.execute("SELECT COUNT(*) FROM profile_runs").fetchone()[0]),
                "health_checks": int(conn.execute("SELECT COUNT(*) FROM health_checks").fetchone()[0]),
                "logs": int(conn.execute("SELECT COUNT(*) FROM runtime_logs").fetchone()[0]),
                "news_items": int(conn.execute("SELECT COUNT(*) FROM news_items").fetchone()[0]),
                "feature_snapshots": int(conn.execute("SELECT COUNT(*) FROM feature_snapshots").fetchone()[0]),
                "metric_snapshots": int(conn.execute("SELECT COUNT(*) FROM metric_snapshots").fetchone()[0]),
                "agent_runs": int(conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]),
                "agent_tasks": int(conn.execute("SELECT COUNT(*) FROM agent_tasks").fetchone()[0]),
                "agent_tool_calls": int(conn.execute("SELECT COUNT(*) FROM agent_tool_calls").fetchone()[0]),
                "agent_trace_spans": int(conn.execute("SELECT COUNT(*) FROM agent_trace_spans").fetchone()[0]),
                "agent_reflections": int(conn.execute("SELECT COUNT(*) FROM agent_reflections").fetchone()[0]),
                "agent_artifacts": int(conn.execute("SELECT COUNT(*) FROM agent_artifacts").fetchone()[0]),
                "real_position_snapshots": int(conn.execute("SELECT COUNT(*) FROM real_position_snapshots").fetchone()[0]),
                "strategy_states": int(conn.execute("SELECT COUNT(*) FROM strategy_states").fetchone()[0]),
                "strategy_changes": int(conn.execute("SELECT COUNT(*) FROM strategy_changes").fetchone()[0]),
            }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        for attempt in range(8):
            try:
                self._init_db_once()
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 7:
                    raise
                time.sleep(0.1 * (attempt + 1))

    def _init_db_once(self) -> None:
        with self._connect() as conn:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  type TEXT NOT NULL,
                  status TEXT NOT NULL,
                  priority INTEGER NOT NULL DEFAULT 0,
                  run_after TEXT NOT NULL,
                  locked_by TEXT NOT NULL DEFAULT '',
                  locked_at TEXT NOT NULL DEFAULT '',
                  attempts INTEGER NOT NULL DEFAULT 0,
                  payload_json TEXT NOT NULL DEFAULT '{}',
                  result_json TEXT NOT NULL DEFAULT '{}',
                  error TEXT NOT NULL DEFAULT '',
                  idempotency_key TEXT NOT NULL UNIQUE,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, run_after, type, priority)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS quote_snapshots (
                  symbol TEXT PRIMARY KEY,
                  quote_json TEXT NOT NULL,
                  previous_quote_json TEXT NOT NULL DEFAULT '',
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cooldowns (
                  key TEXT PRIMARY KEY,
                  last_sent_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS seen_news (
                  key TEXT PRIMARY KEY,
                  first_seen_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS market_events (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  idempotency_key TEXT NOT NULL UNIQUE,
                  symbol TEXT NOT NULL,
                  event_type TEXT NOT NULL,
                  severity TEXT NOT NULL,
                  message TEXT NOT NULL,
                  payload_json TEXT NOT NULL,
                  ai_status TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_market_events_symbol ON market_events(symbol, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS worker_heartbeats (
                  worker_id TEXT PRIMARY KEY,
                  role TEXT NOT NULL,
                  pid INTEGER NOT NULL,
                  status TEXT NOT NULL,
                  last_seen_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runtime_logs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  level TEXT NOT NULL,
                  role TEXT NOT NULL,
                  worker_id TEXT NOT NULL,
                  message TEXT NOT NULL,
                  fields_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_logs_created ON runtime_logs(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_runtime_logs_role_level ON runtime_logs(role, level, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS profile_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  operation TEXT NOT NULL,
                  role TEXT NOT NULL,
                  worker_id TEXT NOT NULL,
                  ok INTEGER NOT NULL,
                  duration_ms REAL NOT NULL,
                  cpu_ms REAL NOT NULL,
                  peak_kb REAL NOT NULL DEFAULT 0,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  error TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_profile_runs_operation ON profile_runs(operation, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_profile_runs_role ON profile_runs(role, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS health_checks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  status TEXT NOT NULL,
                  summary TEXT NOT NULL,
                  details_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_health_checks_created ON health_checks(created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_items (
                  dedupe_key TEXT PRIMARY KEY,
                  title TEXT NOT NULL,
                  url TEXT NOT NULL DEFAULT '',
                  source TEXT NOT NULL DEFAULT '',
                  symbols_text TEXT NOT NULL DEFAULT '',
                  summary TEXT NOT NULL DEFAULT '',
                  kind TEXT NOT NULL DEFAULT 'news',
                  published_at TEXT NOT NULL DEFAULT '',
                  raw_json TEXT NOT NULL DEFAULT '{}',
                  first_seen_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_items_symbols ON news_items(symbols_text, published_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_items_updated ON news_items(updated_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_cache (
                  symbol TEXT NOT NULL,
                  source TEXT NOT NULL DEFAULT 'news',
                  refreshed_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  PRIMARY KEY(symbol, source)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_news_cache_refreshed ON news_cache(refreshed_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feature_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol TEXT NOT NULL,
                  features_json TEXT NOT NULL,
                  source TEXT NOT NULL DEFAULT 'feature_engine',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_feature_snapshots_symbol ON feature_snapshots(symbol, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS metric_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  symbol TEXT NOT NULL,
                  metric_name TEXT NOT NULL,
                  window TEXT NOT NULL DEFAULT 'latest',
                  source TEXT NOT NULL DEFAULT 'metric_service',
                  as_of TEXT NOT NULL,
                  value_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metric_snapshots_symbol ON metric_snapshots(symbol, metric_name, window, as_of)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  agent_name TEXT NOT NULL,
                  role TEXT NOT NULL,
                  objective TEXT NOT NULL,
                  status TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  result_json TEXT NOT NULL DEFAULT '{}',
                  error TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_runs_agent ON agent_runs(agent_name, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_steps (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL,
                  step_name TEXT NOT NULL,
                  status TEXT NOT NULL,
                  summary TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_steps_run ON agent_steps(run_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_tasks (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL,
                  task_id TEXT NOT NULL,
                  name TEXT NOT NULL,
                  description TEXT NOT NULL,
                  status TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_tasks_run ON agent_tasks(run_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_tool_calls (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL,
                  tool_name TEXT NOT NULL,
                  status TEXT NOT NULL,
                  input_json TEXT NOT NULL DEFAULT '{}',
                  output_json TEXT NOT NULL DEFAULT '{}',
                  error TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_run ON agent_tool_calls(run_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_trace_spans (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL,
                  span_name TEXT NOT NULL,
                  status TEXT NOT NULL,
                  summary TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_trace_spans_run ON agent_trace_spans(run_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_memory_links (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL,
                  memory_id INTEGER,
                  layer TEXT NOT NULL,
                  reason TEXT NOT NULL,
                  confidence REAL NOT NULL DEFAULT 0.5,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_memory_links_run ON agent_memory_links(run_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_reflections (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL,
                  kind TEXT NOT NULL,
                  content TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_reflections_run ON agent_reflections(run_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_artifacts (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  run_id INTEGER NOT NULL,
                  artifact_type TEXT NOT NULL,
                  path TEXT NOT NULL,
                  summary TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL,
                  FOREIGN KEY(run_id) REFERENCES agent_runs(id)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_agent_artifacts_run ON agent_artifacts(run_id, id)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS real_position_snapshots (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  source TEXT NOT NULL,
                  positions_json TEXT NOT NULL,
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_real_position_snapshots_created ON real_position_snapshots(created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_states (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  strategy_name TEXT NOT NULL,
                  strategy_version TEXT NOT NULL,
                  status TEXT NOT NULL,
                  config_hash TEXT NOT NULL DEFAULT '',
                  state_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_states_name ON strategy_states(strategy_name, created_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS strategy_changes (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  strategy_name TEXT NOT NULL,
                  strategy_version TEXT NOT NULL,
                  change_type TEXT NOT NULL,
                  summary TEXT NOT NULL,
                  metadata_json TEXT NOT NULL DEFAULT '{}',
                  created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_strategy_changes_name ON strategy_changes(strategy_name, created_at)")

    def _migrate_legacy_files(self) -> None:
        state_path = self.path.parent / "state.json"
        seen_path = self.path.parent / "seen_news.json"
        now = utc_now()
        try:
            state = json.loads(state_path.read_text()) if state_path.exists() else {}
        except (OSError, json.JSONDecodeError):
            state = {}
        with self._connect() as conn:
            for symbol, snapshot in (state.get("quotes") or {}).items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO quote_snapshots(symbol, quote_json, previous_quote_json, updated_at)
                    VALUES (?, ?, '', ?)
                    """,
                    (str(symbol).upper(), dumps(snapshot), str(snapshot.get("timestamp", now)) if isinstance(snapshot, dict) else now),
                )
            for key, last_sent in (state.get("cooldowns") or {}).items():
                conn.execute(
                    "INSERT OR IGNORE INTO cooldowns(key, last_sent_at, updated_at) VALUES (?, ?, ?)",
                    (str(key), str(last_sent), now),
                )
            last_daily = state.get("last_daily_report_date")
            if last_daily:
                conn.execute(
                    "INSERT OR IGNORE INTO cooldowns(key, last_sent_at, updated_at) VALUES (?, ?, ?)",
                    (f"daily_report:{last_daily}", now, now),
                )
            try:
                seen = json.loads(seen_path.read_text()) if seen_path.exists() else []
            except (OSError, json.JSONDecodeError):
                seen = []
            for key in seen:
                conn.execute("INSERT OR IGNORE INTO seen_news(key, first_seen_at) VALUES (?, ?)", (str(key), now))


def runtime_path(data_dir: Path, sqlite_path: str) -> Path:
    path = Path(sqlite_path)
    if path.is_absolute():
        return path
    return data_dir / path


def job_from_row(row: sqlite3.Row) -> JobRecord:
    return JobRecord(
        id=int(row["id"]),
        type=str(row["type"]),
        status=str(row["status"]),
        priority=int(row["priority"]),
        run_after=str(row["run_after"]),
        locked_by=str(row["locked_by"]),
        locked_at=str(row["locked_at"]),
        attempts=int(row["attempts"]),
        payload=json.loads(row["payload_json"] or "{}"),
        result=json.loads(row["result_json"] or "{}"),
        error=str(row["error"]),
        idempotency_key=str(row["idempotency_key"]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def market_event_payload(event: MarketEvent) -> dict[str, Any]:
    return {
        "symbol": event.symbol,
        "event_type": event.event_type,
        "severity": event.severity,
        "message": event.message,
        "quote": quote_to_snapshot(event.quote) if event.quote else None,
        "news": {
            "title": event.news.title,
            "url": event.news.url,
            "source": event.news.source,
            "published_at": event.news.published_at.isoformat() if event.news and event.news.published_at else None,
            "symbols": event.news.symbols,
            "summary": event.news.summary,
            "kind": event.news.kind,
            "raw": event.news.raw,
        }
        if event.news
        else None,
        "created_at": event.created_at.isoformat(),
        "metadata": event.metadata,
    }


def event_idempotency_key(event: MarketEvent) -> str:
    news_key = event.news.dedupe_key() if event.news else ""
    change_key = event.metadata.get("change_percent") if event.metadata else ""
    return f"{event.symbol.upper()}:{event.event_type}:{news_key or change_key or event.message}".lower()


def dumps(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def sanitize_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: sanitize_mapping(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_mapping(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_mapping(item) for item in value]
    if isinstance(value, str):
        return sanitize_secrets(value)
    return value


def sanitize_secrets(value: str) -> str:
    text = str(value)
    patterns = [
        (r"((?:token|api_key|apikey|access_token)=)[^&\s]+", r"\1[REDACTED]"),
        (r"(Bearer\s+)[A-Za-z0-9._\-]+", r"\1[REDACTED]"),
        (r"sk-[A-Za-z0-9_\-]{16,}", "sk-[REDACTED]"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def decode_json_columns(row: dict[str, Any], *columns: str) -> dict[str, Any]:
    for column in columns:
        value = row.get(column)
        if isinstance(value, str):
            try:
                row[column.removesuffix("_json")] = json.loads(value or "{}")
            except json.JSONDecodeError:
                row[column.removesuffix("_json")] = {}
            row.pop(column, None)
    return row


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

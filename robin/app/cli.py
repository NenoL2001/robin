from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl
import typer

from robin.backtest.analytics.artifacts import write_backtest_report
from robin.backtest.engine.simple import run_factor_backtest
from robin.agent.analyzer import analyze_local_context, render_agent_prompt
from robin.contracts.backtest import BacktestRun
from robin.contracts.decision import StrategyDecision
from robin.contracts.op import OpRunContext
from robin.core.config import VNextConfig, load_vnext_config
from robin.core.ids import stable_hash, stable_id
from robin.facts.entity_resolution.security_master import SecurityMaster
from robin.facts.pipeline import build_evidence_packets
from robin.features.backfill import backfill_factors
from robin.features.daily_compute import (
    compute_factor_values_daily,
    write_factor_values,
)
from robin.features.ops import default_op_registry, execute_op
from robin.features.primitives.price import add_price_primitives
from robin.ingest.lake import DataLake
from robin.ingest.run import run_ingest_from_registry_path
from robin.market.loaders.ohlcv import load_ohlcv_csv
from robin.miner.candidates import mine_factor_candidates, write_candidate_outputs
from robin.monitor.lineage.audit import append_lineage_event
from robin.news.pipeline import build_news_event_pipeline
from robin.research.reports.renderer import render_constrained_report
from robin.strategy.execution.reports import execution_report_for_decision
from robin.strategy.risk.policy import risk_check
from robin.strategy.selector.champion_challenger import select_decision

app = typer.Typer(no_args_is_help=True)
ingest_app = typer.Typer(no_args_is_help=True)
facts_app = typer.Typer(no_args_is_help=True)
features_app = typer.Typer(no_args_is_help=True)
replay_app = typer.Typer(no_args_is_help=True)
backtest_app = typer.Typer(no_args_is_help=True)
strategy_app = typer.Typer(no_args_is_help=True)
report_app = typer.Typer(no_args_is_help=True)
audit_app = typer.Typer(no_args_is_help=True)
ops_app = typer.Typer(no_args_is_help=True)
news_app = typer.Typer(no_args_is_help=True)
miner_app = typer.Typer(no_args_is_help=True)
agent_app = typer.Typer(no_args_is_help=True)


def _config(root: Path) -> VNextConfig:
    return load_vnext_config(root=root)


def _echo(payload: dict[str, Any]) -> None:
    typer.echo(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    )


def _default_fixture(name: str) -> Path:
    return Path("tests/fixtures/vnext") / name


def _load_json_contracts(
    path: Path, contract_type: type[BacktestRun] | type[StrategyDecision]
) -> list[Any]:
    if not path.exists():
        return []
    return [
        contract_type.model_validate(json.loads(item.read_text(encoding="utf-8")))
        for item in sorted(path.glob("*.json"))
    ]


@ops_app.command("list")
def ops_list() -> None:
    _echo(
        {"ops": [spec.to_storage_dict() for spec in default_op_registry().list_specs()]}
    )


@ops_app.command("run")
def ops_run(
    name: str,
    ohlcv: Path = typer.Option(_default_fixture("ohlcv.csv"), "--ohlcv"),
    as_of: str = typer.Option("2026-05-01", "--as-of"),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    frame = load_ohlcv_csv(ohlcv)
    ctx = OpRunContext(
        id=stable_id("oprun", {"op": name, "as_of": as_of}),
        run_id="cli",
        snapshot_id=stable_hash({"ohlcv": str(ohlcv), "as_of": as_of}),
        partition_key=as_of,
        asof_ts=f"{as_of}T23:59:59+00:00",
        params={"n": 20},
    )
    op = default_op_registry().get(name)
    result, metadata = execute_op(
        op,
        ctx,
        cache_dir=None
        if dry_run
        else config.lake_root / config.lake.artifact_path / "op_cache",
        bars=frame,
    )
    _echo(
        {
            "dry_run": dry_run,
            "metadata": metadata.to_storage_dict(),
            "rows": result.head(10).to_dicts(),
        }
    )


@news_app.command("build-events")
def news_build_events(
    registry: Path = typer.Option(
        _default_fixture("source_registry.yaml"), "--registry"
    ),
    security_master: Path = typer.Option(
        _default_fixture("security_master.csv"), "--security-master"
    ),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    lake = DataLake(config)
    lake.initialize()
    if not dry_run:
        run_ingest_from_registry_path(config, registry)
    result = build_news_event_pipeline(
        lake.read_raw_documents(), SecurityMaster.from_csv(security_master)
    )
    _echo(
        {
            "dry_run": dry_run,
            "claims": len(result.claims),
            "events": len(result.events),
            "packets": len(result.packets),
            "factor_rows": result.factor_frame.height,
        }
    )


@miner_app.command("run")
def miner_run(
    factor: list[str] = typer.Option(["return_1d"], "--factor"),
    ohlcv: Path = typer.Option(_default_fixture("ohlcv.csv"), "--ohlcv"),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    frame = add_price_primitives(load_ohlcv_csv(ohlcv)).with_columns(
        ((pl.col("close").shift(-1).over("symbol") / pl.col("close")) - 1.0)
        .fill_null(0.0)
        .alias("forward_return")
    )
    frame = frame.rename({"symbol": "entity_id"})
    decisions = mine_factor_candidates(
        frame, factor, min_observations=2, min_abs_rank_ic=0.0, max_q_value=1.0
    )
    paths = []
    if not dry_run:
        paths = [
            str(path)
            for path in write_candidate_outputs(
                decisions, config.lake_root / config.lake.artifact_path / "miner"
            )
        ]
    _echo(
        {
            "dry_run": dry_run,
            "decisions": [decision.to_dict() for decision in decisions],
            "paths": paths,
        }
    )


@agent_app.command("analyze")
def agent_analyze(
    context: Path | None = typer.Option(None, "--context"),
    render_prompt: bool = typer.Option(False, "--render-prompt"),
) -> None:
    payload = (
        json.loads(context.read_text(encoding="utf-8"))
        if context
        else {"evidence_packets": [], "factor_metrics": []}
    )
    if render_prompt:
        typer.echo(render_agent_prompt(payload))
    else:
        _echo(analyze_local_context(payload).model_dump())


@ingest_app.command("run")
def ingest_run(
    registry: Path = typer.Option(
        _default_fixture("source_registry.yaml"),
        "--registry",
        help="Source registry YAML.",
    ),
    root: Path = typer.Option(Path("."), "--root", help="Repository or run root."),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    lake = DataLake(config)
    lake.initialize()
    if dry_run:
        _echo(
            {
                "dry_run": True,
                "registry": str(registry),
                "lake_root": str(config.lake_root),
            }
        )
        return
    _echo(run_ingest_from_registry_path(config, registry))


@facts_app.command("build-evidence")
def facts_build_evidence(
    security_master: Path = typer.Option(
        _default_fixture("security_master.csv"), "--security-master"
    ),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    lake = DataLake(config)
    raw_documents = lake.read_raw_documents()
    packets = build_evidence_packets(
        raw_documents, SecurityMaster.from_csv(security_master)
    )
    if not dry_run:
        lake.write_evidence_packets(packets)
    _echo(
        {
            "dry_run": dry_run,
            "raw_documents": len(raw_documents),
            "evidence_packets": len(packets),
            "packet_ids": [packet.id for packet in packets],
        }
    )


@features_app.command("compute-daily")
def features_compute_daily(
    ohlcv: Path = typer.Option(_default_fixture("ohlcv.csv"), "--ohlcv"),
    as_of: str | None = typer.Option(None, "--as-of"),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    frame = load_ohlcv_csv(ohlcv)
    run_date = (
        date.fromisoformat(as_of)
        if as_of
        else frame.select(pl.col("date").max()).item()
    )
    values = compute_factor_values_daily(frame, run_date)
    output_dir = config.lake_root / config.lake.gold_path / "factor_values"
    path = "" if dry_run else str(write_factor_values(values, output_dir))
    _echo(
        {
            "dry_run": dry_run,
            "as_of": run_date.isoformat(),
            "factor_values": len(values),
            "path": path,
        }
    )


@features_app.command("backfill")
def features_backfill(
    start: str,
    end: str,
    ohlcv: Path = typer.Option(_default_fixture("ohlcv.csv"), "--ohlcv"),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    frame = load_ohlcv_csv(ohlcv)
    output_dir = config.lake_root / config.lake.gold_path / "factor_values"
    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end)
    if dry_run:
        _echo(
            {
                "dry_run": True,
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "symbols": frame.get_column("symbol").n_unique(),
            }
        )
        return
    paths = backfill_factors(frame, start_date, end_date, output_dir)
    _echo({"dry_run": False, "paths": [str(path) for path in paths]})


@replay_app.command("run")
def replay_run(
    registry: Path = typer.Option(
        _default_fixture("source_registry.yaml"), "--registry"
    ),
    security_master: Path = typer.Option(
        _default_fixture("security_master.csv"), "--security-master"
    ),
    ohlcv: Path = typer.Option(_default_fixture("ohlcv.csv"), "--ohlcv"),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    lake = DataLake(config)
    lake.initialize()
    ingest_result = (
        {"dry_run": True}
        if dry_run
        else run_ingest_from_registry_path(config, registry)
    )
    raw_documents = lake.read_raw_documents() if not dry_run else []
    packets = build_evidence_packets(
        raw_documents, SecurityMaster.from_csv(security_master)
    )
    frame = load_ohlcv_csv(ohlcv)
    run_date = frame.select(pl.col("date").max()).item()
    factor_values = compute_factor_values_daily(frame, run_date)
    if not dry_run:
        lake.write_evidence_packets(packets)
        write_factor_values(
            factor_values, config.lake_root / config.lake.gold_path / "factor_values"
        )
    replay_hash = stable_hash(
        {
            "raw": [doc.id for doc in raw_documents],
            "packets": [packet.id for packet in packets],
            "factors": [value.id for value in factor_values],
        }
    )
    _echo(
        {
            "dry_run": dry_run,
            "ingest": ingest_result,
            "raw_documents": len(raw_documents),
            "evidence_packets": len(packets),
            "factor_values": len(factor_values),
            "replay_hash": replay_hash,
        }
    )


@backtest_app.command("run")
def backtest_run(
    factor: str = typer.Option("return_1d", "--factor"),
    ohlcv: Path = typer.Option(_default_fixture("ohlcv.csv"), "--ohlcv"),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    frame = add_price_primitives(load_ohlcv_csv(ohlcv)).with_columns(
        ((pl.col("close").shift(-1).over("symbol") / pl.col("close")) - 1.0)
        .fill_null(0.0)
        .alias("forward_return")
    )
    run = run_factor_backtest(
        frame,
        "vnext_fixture_strategy",
        factor,
        artifact_dir=config.lake_root / config.lake.artifact_path / "backtests",
    )
    path = ""
    mlflow_uri = ""
    if not dry_run:
        path = str(
            write_backtest_report(
                run, config.lake_root / config.lake.artifact_path / "backtests"
            )
        )
        mlflow_uri = _log_backtest_mlflow(run, config)
    _echo(
        {
            "dry_run": dry_run,
            "backtest_run_id": run.id,
            "gross": run.gross_metrics,
            "net": run.net_metrics,
            "path": path,
            "mlflow_uri": mlflow_uri,
        }
    )


def _log_backtest_mlflow(run: BacktestRun, config: VNextConfig) -> str:
    try:
        import mlflow

        tracking_uri = (
            (config.lake_root / config.lake.artifact_path / "mlruns").resolve().as_uri()
        )
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment("robin-vnext-local")
        with mlflow.start_run(run_name=run.id):
            mlflow.log_params(
                {
                    "strategy_name": run.strategy_name,
                    "factor_set_hash": run.factor_set_hash,
                }
            )
            for key, value in run.gross_metrics.items():
                mlflow.log_metric(f"gross_{key}", value)
            for key, value in run.net_metrics.items():
                mlflow.log_metric(f"net_{key}", value)
        return tracking_uri
    except (
        Exception
    ) as exc:  # pragma: no cover - depends on optional local mlflow runtime.
        fallback = (
            config.lake_root
            / config.lake.artifact_path
            / "mlflow.UNSPECIFIED_UNAVAILABLE.txt"
        )
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(str(exc), encoding="utf-8")
        return str(fallback)


@strategy_app.command("decide")
def strategy_decide(
    symbol: str,
    strategy_name: str = typer.Option("vnext_champion", "--strategy-name"),
    max_notional: float = typer.Option(0.0, "--max-notional"),
    root: Path = typer.Option(Path("."), "--root"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    lake = DataLake(config)
    packets = lake.read_evidence_packets()
    factor_values = [
        value for value in lake.read_factor_values() if value.symbol == symbol.upper()
    ]
    decision = risk_check(
        select_decision(strategy_name, symbol, packets, factor_values),
        max_notional=max_notional,
    )
    execution = execution_report_for_decision(
        decision, broker_permission=config.broker_permission
    )
    if not dry_run:
        output = (
            config.lake_root
            / config.lake.artifact_path
            / "decisions"
            / f"{decision.id}.json"
        )
        lake.write_json_artifact(output, decision)
        lake.write_json_artifact(output.with_name(f"{execution.id}.json"), execution)
    _echo(
        {
            "dry_run": dry_run,
            "decision": decision.to_storage_dict(),
            "execution": execution.to_storage_dict(),
        }
    )


@report_app.command("render")
def report_render(
    root: Path = typer.Option(Path("."), "--root"),
    output: Path | None = typer.Option(None, "--output"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    config = _config(root)
    lake = DataLake(config)
    report = render_constrained_report(
        lake.read_evidence_packets(),
        lake.read_factor_values(),
        _load_json_contracts(
            config.lake_root / config.lake.artifact_path / "backtests", BacktestRun
        ),
        _load_json_contracts(
            config.lake_root / config.lake.artifact_path / "decisions", StrategyDecision
        ),
    )
    if output and not dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
    typer.echo(report)


@audit_app.command("lineage")
def audit_lineage(
    artifact_id: str = typer.Option("manual", "--artifact-id"),
    input_id: list[str] = typer.Option([], "--input-id"),
    code_hash: str = typer.Option("UNSPECIFIED_CODE_HASH", "--code-hash"),
    config_hash: str = typer.Option("UNSPECIFIED_CONFIG_HASH", "--config-hash"),
    root: Path = typer.Option(Path("."), "--root"),
) -> None:
    config = _config(root)
    event = append_lineage_event(
        config.lake_root / config.lake.artifact_path / "lineage.jsonl",
        artifact_id=artifact_id,
        inputs=input_id,
        code_hash=code_hash,
        config_hash=config_hash,
    )
    _echo(event)


app.add_typer(ingest_app, name="ingest")
app.add_typer(facts_app, name="facts")
app.add_typer(features_app, name="features")
app.add_typer(replay_app, name="replay")
app.add_typer(backtest_app, name="backtest")
app.add_typer(strategy_app, name="strategy")
app.add_typer(report_app, name="report")
app.add_typer(audit_app, name="audit")
app.add_typer(ops_app, name="ops")
app.add_typer(news_app, name="news")
app.add_typer(miner_app, name="miner")
app.add_typer(agent_app, name="agent")


if __name__ == "__main__":
    app()

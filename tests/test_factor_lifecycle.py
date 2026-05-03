from __future__ import annotations

from datetime import datetime, timezone

import pytest

from portfolio_bot.config import load_config
from portfolio_bot.market.news_strategy import FactorCandidateProposal
from portfolio_bot.models import NewsItem, Quote
from portfolio_bot.strategies.factor_attribution import FactorAttributionSummary
from portfolio_bot.strategies.factor_formulas import FactorFormulaRegistry
from portfolio_bot.strategies.factor_specs import FactorLifecyclePolicy, FactorSpec, FactorSpecStore, iterate_factor_specs
from portfolio_bot.strategies.factor_validation import validate_factor_flow
from portfolio_bot.strategies.semiconductor_reversal import SemiconductorReversalStrategy


def test_factor_spec_store_crud_backup_and_hard_delete_confirm(tmp_path):
    config = write_config(tmp_path)
    store = FactorSpecStore(config.strategy_root)
    spec = FactorSpec("test_factor", weight=1.25, status="candidate", evidence_required=False)

    added = store.upsert(spec, reason="test")
    updated = store.update("test_factor", {"weight": 2.0}, reason="test update")
    status = store.set_status("test_factor", "quarantined", reason="bad posterior")

    assert added.action == "added"
    assert updated.action == "updated"
    assert status.action == "quarantined"
    assert store.get("test_factor").weight == 2.0
    assert store.path.with_suffix(store.path.suffix + ".bak").exists()

    with pytest.raises(ValueError):
        store.hard_delete("test_factor", confirm="wrong")
    deleted = store.hard_delete("test_factor", confirm="test_factor")

    assert deleted.action == "hard_deleted"
    assert store.get("test_factor") is None


def test_factor_lifecycle_promotes_quarantines_and_retires(tmp_path):
    config = write_config(tmp_path)
    policy = FactorLifecyclePolicy(config)
    now = "2026-05-03T00:00:00+00:00"
    existing = {
        "earnings_surprise": FactorSpec("earnings_surprise", status="candidate", weight=10.0, min_observations_for_orders=2),
        "guidance_revision": FactorSpec("guidance_revision", status="active", weight=10.0, negative_streak=1),
        "liquidity_break_risk": FactorSpec("liquidity_break_risk", status="quarantined", weight=-4.0, negative_streak=1),
    }

    specs, mutations, _ = policy.run(
        existing,
        candidate_proposals=[],
        attribution_summary=[
            FactorAttributionSummary("earnings_surprise", 2, 10.0, 2.0, 1.0),
            FactorAttributionSummary("guidance_revision", 2, 10.0, -2.0, -1.0),
            FactorAttributionSummary("liquidity_break_risk", 2, -4.0, 2.0, -1.0),
        ],
        now=now,
    )
    by_name = {spec.name: spec for spec in specs}
    actions = {(mutation.factor_name, mutation.action) for mutation in mutations}

    assert by_name["earnings_surprise"].status == "active"
    assert by_name["guidance_revision"].status == "quarantined"
    assert by_name["liquidity_break_risk"].status == "retired"
    assert ("earnings_surprise", "promoted") in actions
    assert ("guidance_revision", "quarantined") in actions
    assert ("liquidity_break_risk", "retired") in actions


def test_factor_iteration_persists_news_candidate(tmp_path):
    config = write_config(tmp_path)
    proposal = FactorCandidateProposal(
        name="custom_event_factor",
        direction="positive",
        weight=4.0,
        reason="analyst revisions after official event",
        evidence_event_types=("analyst_revision",),
        min_observations_for_orders=2,
    )

    result = iterate_factor_specs(config, dry_run=False, candidate_proposals=[proposal])
    stored = FactorSpecStore(config.strategy_root).get("custom_event_factor")

    assert stored is not None
    assert stored.status == "candidate"
    assert "custom_event_factor" in result.added
    assert any(item["factor_name"] == "custom_event_factor" for item in result.formula_proposals)


def test_candidate_and_retired_factors_do_not_contribute_to_official_score():
    strategy = SemiconductorReversalStrategy()
    quote = Quote("SNDK", 100.0, datetime.now(timezone.utc), change_percent=4.0)
    news = [
        NewsItem(
            title="SNDK earnings beat and raises guidance",
            url="https://investor.sandisk.com/node/7896/pdf",
            source="investor.sandisk.com",
            symbols=["SNDK"],
            summary="Revenue was above guidance and outlook improved.",
            raw={"event_types": ["earnings_surprise", "guidance_revision"], "source_tier": "P0_official"},
        )
    ]

    score = strategy.evaluate(
        "SNDK",
        quote,
        news,
        features={
            "factor_weights": {"earnings_surprise": 14.0, "guidance_revision": 12.0},
            "factor_statuses": {"earnings_surprise": "candidate", "guidance_revision": "retired"},
        },
    )
    factors = {row["name"]: row for row in score.metadata["factor_breakdown"]}

    assert factors["earnings_surprise"]["contribution"] == 0.0
    assert factors["earnings_surprise"]["shadow_contribution"] == 14.0
    assert factors["guidance_revision"]["contribution"] == 0.0


def test_formula_registry_and_validation_detect_hash_mismatch():
    registry = FactorFormulaRegistry.default()
    spec = FactorSpec("earnings_surprise", status="active")
    spec.formula_hash = "bad-hash"
    result = validate_factor_flow(
        {"signals": [{"symbol": "SNDK", "score": 10, "metadata": {"factor_breakdown": [], "risk_gate_verdict": {"allowed": False}}}]},
        [spec],
        proposed_factors=[],
        attribution_summary=[],
        dry_run=True,
    )

    assert registry.exists("builtin.earnings_surprise")
    assert not result.ok
    assert any("formula_hash mismatch" in issue.message for issue in result.issues)


def write_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
timezone: UTC
strategy_lab:
  min_factor_observations_for_orders: 2
  factor_quarantine_observations: 4
memory:
  enabled: false
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    (tmp_path / "strategy_skills").mkdir(exist_ok=True)
    (tmp_path / "analysts.yaml").write_text("analysts: []\n", encoding="utf-8")
    (tmp_path / "holdings.yaml").write_text("holdings: []\n", encoding="utf-8")
    return load_config(config_path)

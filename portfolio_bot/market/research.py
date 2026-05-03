from __future__ import annotations

import json
import uuid
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from ..config import BotConfig
from ..backtest import BacktestStore, format_backtest_result
from .data_hub import DataHub
from .daily_digest import DailyNewsDigestBuilder, dedupe_news_items
from .bars import BarStore
from ..data.finnhub import FinnhubClient
from ..data.tradier import TradierClient
from ..data.x_api import XApiClient
from .evidence_ranker import EvidenceRanker
from .features import FeatureEngine
from ..models import Holding, NewsItem, OptionCandidate, PaperOrderProposal, Quote, RiskGateVerdict, StrategyScore, StrategySignal
from ..memory import MemoryStore, OpenSourceMemoryBridge, memory_path
from ..openai_client import OpenAIService
from ..paper import PaperBroker
from ..runtime import RuntimeStore, runtime_path
from .relations import RelationGraph, StoredSymbolRelation, static_symbol_relations, stored_relation_from_scout
from .report_verifier import ReportVerifier, fallback_verified_report
from ..storage import Storage, load_holdings
from ..strategies.registry import load_strategies, load_strategy_infos
from ..strategies.factor_attribution import FactorAttributionStore
from ..strategies.risk_gate import RiskGateContext, StrategyRiskGate
from .exposures import LEVERAGED_EXPOSURES, expand_leveraged_symbols, leveraged_exposure
from .metrics import news_relevance, symbol_matches_text
from .news_analysis import analyze_news_items, format_analyzed_news_section
from .news_strategy import propose_factor_candidates_from_news
from .strategy_news_scout import StrategyNewsScout, StrategyScoutResult, SymbolRelationship
from ..strategies.factor_validation import validate_factor_flow
from ..strategies.factor_specs import FactorSpecStore, iterate_factor_specs


class ResearchEngine:
    def __init__(
        self,
        config: BotConfig,
        finnhub: FinnhubClient | None = None,
        tradier: TradierClient | None = None,
        x_api: XApiClient | None = None,
        openai_service: OpenAIService | None = None,
        strategy_news_scout: StrategyNewsScout | None = None,
    ):
        self.config = config
        self.storage = Storage(config.data_dir)
        self.finnhub = finnhub or FinnhubClient(config.finnhub_api_key)
        self.tradier = tradier or TradierClient(config.tradier_access_token, config.tradier_base_url)
        self.x_api = x_api or XApiClient(config.x_bearer_token)
        self.openai = openai_service or OpenAIService(config)
        self.data_hub = DataHub(config, runtime=RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path)), finnhub=self.finnhub, x_api=self.x_api)
        self.current_holdings = load_holdings(config.holdings_path)
        self.strategies = load_strategies(config.strategy_root, config.research, holdings=self.current_holdings)
        self.memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        self.runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.bar_store = BarStore.from_config(config, memory=self.memory) if config.market_bars.enabled else None
        self.relation_graph = RelationGraph.from_config(config, memory=self.memory) if config.relation_graph.enabled else None
        self.evidence_ranker = EvidenceRanker(config, memory=self.memory)
        self.report_verifier = ReportVerifier(config, memory=self.memory)
        self.factor_attribution = FactorAttributionStore.from_config(config, memory=self.memory)
        self.features = FeatureEngine(self.runtime, backend=config.metrics.backend, max_workers=config.metrics.max_workers, bar_store=self.bar_store)
        self.open_memory = OpenSourceMemoryBridge(config.memory.open_source_enabled, config.memory.open_source_backend)
        self.paper = PaperBroker(config.data_dir / config.paper.sqlite_path, config.paper.starting_cash, memory=self.memory)
        self.backtests = BacktestStore(config.data_dir / config.backtest.sqlite_path, memory=self.memory)
        self.strategy_news_scout = strategy_news_scout or StrategyNewsScout(config, runtime=self.runtime, memory=self.memory)
        self.factor_specs = FactorSpecStore(config.strategy_root)

    def collect_news(self, symbols: list[str], days: int = 3, commit: bool = True) -> list[NewsItem]:
        fresh = self.data_hub.collect_news(symbols, days=days, commit=commit)
        for item in fresh:
            if not commit:
                continue
            symbols_text = ",".join(sorted(set(item.symbols)))
            self._remember_once_today(
                "news_lead",
                f"{item.source}: {item.title} {item.summary}".strip(),
                symbol=item.symbols[0].upper() if item.symbols else "",
                importance=0.7 if item.kind == "x_post" else 0.6,
                confidence=0.35 if item.kind == "x_post" else 0.6,
                source=item.source,
                metadata={"url": item.url, "symbols": symbols_text, "kind": item.kind},
            )
        return fresh

    def score_universe(
        self,
        symbols: list[str],
        quotes: dict[str, Quote | None],
        news: list[NewsItem],
        feature_map: dict[str, dict] | None = None,
        *,
        holdings: list[Holding] | None = None,
        commit: bool = True,
    ) -> list[StrategyScore]:
        feature_map = feature_map or self.features.compute_many(symbols, quotes, news, holdings=holdings or self.current_holdings)
        factor_weights = self.factor_specs.weights()
        if factor_weights:
            feature_map = {
                symbol: {**dict(features or {}), "factor_weights": factor_weights}
                for symbol, features in feature_map.items()
            }
        by_symbol = news_by_symbol(news, symbols)
        scores: list[StrategyScore] = []
        strategies = self.strategies if holdings is None else load_strategies(self.config.strategy_root, self.config.research, holdings=holdings)
        for strategy in strategies:
            for symbol in symbols:
                score = strategy.evaluate(symbol, quotes.get(symbol), by_symbol.get(symbol, []), fundamentals={}, features=feature_map.get(symbol.upper(), {}))
                scores.append(score)
                if commit:
                    self.storage.append_memory(
                        {
                            "type": "strategy_score",
                            "timestamp": datetime.now(timezone.utc),
                            "strategy": strategy.name,
                            "symbol": symbol,
                            "score": score.score,
                            "confidence": score.confidence,
                            "catalysts": score.catalysts,
                            "risk_flags": score.risk_flags,
                        }
                    )
                    content = (
                        f"{symbol} strategy score {score.score:.1f}, confidence {score.confidence:.2f}. "
                        f"Bull: {score.bull_case} Bear: {score.bear_case} "
                        f"Catalysts: {', '.join(score.catalysts) or 'none'} "
                        f"Risks: {', '.join(score.risk_flags) or 'none'}"
                    )
                    self._remember_once_today(
                        "signal",
                        signal_text(symbol, score),
                        symbol=symbol,
                        strategy=strategy.name,
                        importance=min(1.0, max(0.25, score.score / 100.0)),
                        confidence=score.confidence,
                        source="strategy",
                        metadata={"action": action_for_score(score.score), "score": score.score, "risk_flags": score.risk_flags},
                    )
                    self._remember_once_today(
                        "strategy_score",
                        content,
                        symbol=symbol,
                        strategy=strategy.name,
                        importance=min(1.0, max(0.2, score.score / 100.0)),
                        confidence=score.confidence,
                        source="strategy",
                        metadata={"score": score.score, "catalysts": score.catalysts, "risk_flags": score.risk_flags},
                    )
                    self.open_memory.add(content, metadata={"symbol": symbol, "strategy": strategy.name, "kind": "strategy_score"})
        return sorted(scores, key=lambda item: item.score, reverse=True)

    def long_call_candidates(self, symbols: list[str], quotes: dict[str, Quote | None], news: list[NewsItem]) -> list[OptionCandidate]:
        candidates: list[OptionCandidate] = []
        if not self.tradier.configured:
            return candidates
        by_symbol = news_by_symbol(news, symbols)
        now = datetime.now(timezone.utc)
        for symbol in symbols:
            expirations = [
                exp for exp in self.tradier.expirations(symbol)
                if self.config.research.option_min_days <= (exp - now).days <= self.config.research.option_max_days
            ]
            contracts = []
            for expiration in expirations:
                contracts.extend(self.tradier.option_chain(symbol, expiration))
            for strategy in self.strategies:
                candidates.extend(strategy.rank_options(symbol, quotes.get(symbol), contracts, by_symbol.get(symbol, [])))
        return sorted(candidates, key=lambda item: item.score, reverse=True)

    def generate_daily_digest(self, symbols: list[str], *, days: int = 3, dry_run: bool = False, include_web: bool | None = None):
        if include_web is None:
            include_web = not dry_run
        return DailyNewsDigestBuilder(self.config, data_hub=self.data_hub, runtime=self.runtime, memory=self.memory).build(symbols, days=days, commit=not dry_run, include_web=include_web)

    def refresh_bars(self, symbols: list[str], *, dry_run: bool = False) -> dict[str, object]:
        normalized = sorted({_normalize_research_symbol(symbol) for symbol in symbols if symbol})
        if not self.bar_store:
            return {"enabled": False, "symbols": normalized, "count": 0}
        quotes = self.data_hub.quotes(normalized, commit=not dry_run)
        bars = self.bar_store.refresh_from_quotes(quotes, commit=not dry_run)
        return {"enabled": True, "symbols": normalized, "count": len(bars), "bars": [bar.to_dict() for bar in bars.values()]}

    def discover_relations(self, symbols: list[str], *, dry_run: bool = False) -> dict[str, object]:
        normalized = sorted({_normalize_research_symbol(symbol) for symbol in symbols if symbol})
        scout = self.scout_strategy_news(normalized, dry_run=dry_run, deep=True)
        stored = self.relation_graph.upsert_many_from_scout(scout.relationships, remember=not dry_run) if self.relation_graph and not dry_run else []
        preview = [stored_relation_from_scout(item) for item in scout.relationships] if self.relation_graph and dry_run else []
        static = self.relation_graph.seed_static(remember=False) if self.relation_graph and not dry_run else []
        preview_static = static_symbol_relations() if self.relation_graph and dry_run else []
        return {
            "symbols": normalized,
            "scout": scout.to_dict(),
            "relationships": [item.to_dict() for item in [*stored, *preview, *static, *preview_static]],
        }

    def scout_strategy_news(
        self,
        symbols: list[str],
        *,
        strategy_name: str = "semiconductor_reversal",
        dry_run: bool = False,
        deep: bool = False,
        allow_external: bool = True,
    ) -> StrategyScoutResult:
        result = self.strategy_news_scout.scout(strategy_name, symbols, commit=not dry_run, deep=deep, allow_external=allow_external)
        if self.relation_graph and not dry_run:
            self.relation_graph.upsert_many_from_scout(result.relationships, remember=not dry_run)
        return result

    def iterate_strategy_factors(self, *, dry_run: bool = False):
        return iterate_factor_specs(self.config, dry_run=dry_run)

    def validate_strategy_factor_flow(
        self,
        symbols: list[str] | None = None,
        *,
        holdings: list[Holding] | None = None,
        dry_run: bool = True,
    ):
        holdings = holdings if holdings is not None else self.current_holdings
        plan = self.generate_strategy_plan(symbols, holdings=holdings, dry_run=True)
        specs = self.factor_specs.load()
        if not specs:
            specs = self.iterate_strategy_factors(dry_run=True).specs
        existing_names = {item.name for item in specs}
        digest = plan.get("digest", {}) if isinstance(plan.get("digest"), dict) else {}
        digest_items = []
        digest_rows = digest.get("items", []) if isinstance(digest, dict) else []
        for row in digest_rows:
            if not isinstance(row, dict):
                continue
            published = row.get("published_at")
            published_at = None
            if published:
                try:
                    published_at = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
                except ValueError:
                    published_at = None
            digest_items.append(
                NewsItem(
                    title=str(row.get("title", "")),
                    url=str(row.get("url", "")),
                    source=str(row.get("source", "")),
                    published_at=published_at,
                    symbols=[str(value) for value in row.get("symbols", []) or []],
                    summary=str(row.get("summary", "")),
                    kind=str(row.get("kind", "news")),
                    raw=dict(row.get("raw", {}) or {}),
                )
            )
        proposed = propose_factor_candidates_from_news(digest_items, existing_names)
        attribution = self.factor_attribution.summary(horizon="1d", min_observations=1)
        result = validate_factor_flow(
            plan,
            specs,
            proposed_factors=proposed,
            attribution_summary=attribution,
            dry_run=dry_run,
        )
        if not dry_run:
            self.memory.add(
                "factor_flow_validation",
                result.summary(),
                symbol=",".join(symbols or []),
                strategy="strategy_lab",
                importance=0.7,
                confidence=0.75,
                source="factor_validation",
                metadata=result.to_dict(),
            )
        return {"validation": result.to_dict(), "validation_summary": result.summary(), "plan_summary": plan.get("summary", "")}

    def generate_strategy_plan(
        self,
        symbols: list[str] | None = None,
        *,
        holdings: list[Holding] | None = None,
        dry_run: bool = False,
    ) -> dict[str, object]:
        holdings = holdings if holdings is not None else self.current_holdings
        if symbols:
            symbol_set = {_normalize_research_symbol(symbol) for symbol in symbols if symbol}
        else:
            symbol_set = direct_research_symbols(holdings) | set(self.config.research.default_universe)
        normalized = sorted(symbol for symbol in symbol_set if symbol)
        if self.config.strategy_lab.daily_factor_iteration_enabled:
            self.iterate_strategy_factors(dry_run=dry_run)
        scout = self.scout_strategy_news(normalized, dry_run=dry_run, deep=True, allow_external=True)
        graph_related = self.relation_graph.related_symbols(normalized, min_confidence=self.config.relation_graph.min_confidence) if self.relation_graph else set()
        graph_relationships = self.relation_graph.relationships_for(set(normalized) | graph_related, min_confidence=self.config.relation_graph.min_confidence) if self.relation_graph else []
        research_universe = sorted(expand_leveraged_symbols(set(normalized) | set(scout.related_symbols()) | graph_related))
        quotes = self.data_hub.quotes(research_universe, commit=not dry_run)
        if self.bar_store:
            self.bar_store.refresh_from_quotes(quotes, commit=not dry_run)
        digest = self.generate_daily_digest(research_universe, days=5, dry_run=dry_run)
        all_relationships = [*scout.relationships, *stored_relations_to_scout(graph_relationships)]
        news = bridge_related_news(dedupe_news_items([*digest.items, *scout.news_items]), all_relationships, normalized)
        news = self.evidence_ranker.top_news_items(news, research_universe, commit=not dry_run)
        feature_map = self.features.compute_many(normalized, quotes, news, holdings=holdings, commit=not dry_run)
        scores = self.score_universe(normalized, quotes, news, feature_map, holdings=holdings, commit=not dry_run)
        if not dry_run:
            self._persist_factor_observations(scores)
        by_symbol = news_by_symbol(news, normalized)
        latest_backtests = {result.strategy_name: result for result in self.backtests.recent(limit=20)}
        real_exposure = exposure_value_by_symbol(holdings)
        paper_exposure = paper_exposure_by_symbol(self.paper.positions())
        equity = current_paper_equity(self.paper)
        drawdown = paper_drawdown(self.paper, current_equity=equity)
        gate = StrategyRiskGate(self.config.strategy_risk)
        versions = {info.name: info.version for info in load_strategy_infos(self.config.strategy_root)}
        signals: list[StrategySignal] = []
        verdicts: list[RiskGateVerdict] = []
        proposals: list[PaperOrderProposal] = []
        for score in scores:
            quote = quotes.get(score.symbol) or quotes.get(score.symbol.upper())
            signal = strategy_signal_from_score(score, strategy_version=versions.get(score.strategy, "1.0.0"))
            signal.metadata.update(
                {
                    "factor_breakdown": score.metadata.get("factor_breakdown", []),
                    "features": score.metadata.get("features", {}),
                    "evidence_links": [item.url for item in by_symbol.get(score.symbol, []) if item.url][:12],
                    "risk_flags": score.risk_flags,
                    "strategy_scout_queries": scout.queries,
                    "strategy_scout_events": [event.to_dict() for event in scout.events if event_matches_signal(event.symbol, score.symbol)],
                }
            )
            if any(row.get("name") == "earnings_surprise" for row in score.metadata.get("factor_breakdown", [])):
                signal.metadata["requires_official_source"] = self.config.strategy_risk.require_official_source_for_earnings_orders
            verdict = gate.evaluate(
                signal,
                RiskGateContext(
                    portfolio_equity=equity,
                    paper_drawdown=drawdown,
                    real_exposure=merged_exposure_value(real_exposure, score.symbol, graph_relationships),
                    paper_exposure=merged_exposure_value(paper_exposure, score.symbol, graph_relationships),
                    asset_type="equity",
                    latest_backtest=latest_backtests.get(score.strategy),
                    evidence=by_symbol.get(score.symbol, []),
                    price=quote.price if quote else None,
                    intraday_change_percent=quote.change_percent if quote else None,
                ),
            )
            proposal = paper_order_proposal_from_signal(signal, verdict, quote)
            signal.metadata["risk_gate_verdict"] = asdict(verdict)
            signal.metadata["proposed_paper_size"] = verdict.max_notional
            signal.metadata["paper_order_proposal_id"] = proposal.proposal_id if proposal else ""
            signals.append(signal)
            verdicts.append(verdict)
            if proposal:
                proposals.append(proposal)
            if not dry_run:
                self._persist_strategy_signal(signal, verdict, proposal)
                self.factor_attribution.record_signal(signal, quote, remember=True)
        paper_order_jobs = []
        if not dry_run and proposals:
            paper_order_jobs = self.enqueue_paper_order_proposals(proposals)
        summary = render_strategy_plan_summary(signals, verdicts, proposals, drawdown, paper_order_jobs=paper_order_jobs)
        if not dry_run:
            self.runtime.save_metric_snapshot(
                "PORTFOLIO",
                "strategy_plan",
                {
                    "summary": summary,
                    "signals": [asdict(item) for item in signals],
                    "risk_gate_verdicts": [asdict(item) for item in verdicts],
                    "paper_order_proposals": [asdict(item) for item in proposals],
                    "paper_order_jobs": paper_order_jobs,
                    "paper_drawdown": drawdown,
                    "strategy_scout": scout.to_dict(),
                },
                source="strategy_plan",
            )
        return {
            "summary": summary,
            "signals": [asdict(item) for item in signals],
            "risk_gate_verdicts": [asdict(item) for item in verdicts],
            "paper_order_proposals": [asdict(item) for item in proposals],
            "paper_order_jobs": paper_order_jobs,
            "strategy_scout": scout.to_dict(),
            "digest": digest.to_dict(),
            "paper_drawdown": drawdown,
        }

    def generate_strategy_roundtable(self, *, dry_run: bool = False) -> str:
        today = datetime.now(timezone.utc).date().isoformat()
        run_id = None if dry_run else self.runtime.start_agent_run("strategy_roundtable", "strategy", "daily asynchronous strategy/source/risk/report roundtable", metadata={"date": today})
        recent_digest = self.memory.recent(kind="daily_news_summary", limit=1)
        recent_gates = self.memory.recent(kind="risk_gate_verdict", limit=20)
        recent_proposals = self.memory.recent(kind="paper_order_proposal", limit=20)
        recent_reviews = self.memory.recent(kind="daily_review", limit=3)
        sections = {
            "research_agent": roundtable_source_view(self.config, recent_digest),
            "risk_agent": roundtable_risk_view(recent_gates, self.paper, self.config.paper.starting_cash),
            "report_agent": roundtable_report_view(recent_reviews, recent_digest),
            "strategy_agent": roundtable_strategy_view(recent_gates, recent_proposals),
        }
        memo = (
            "策略圆桌每日纪要\n\n"
            f"## Research Agent\n{sections['research_agent']}\n\n"
            f"## Risk Agent\n{sections['risk_agent']}\n\n"
            f"## Report Agent\n{sections['report_agent']}\n\n"
            f"## Strategy Agent Decision\n{sections['strategy_agent']}"
        )
        if not dry_run and run_id is not None:
            for name, content in sections.items():
                self.runtime.add_agent_trace_span(run_id, name, "done", content[:2000], {"roundtable_date": today})
                self.runtime.add_agent_reflection(run_id, name, content[:3000], {"roundtable_date": today})
            self.runtime.update_agent_run(run_id, status="done", result={"summary": memo, "sections": sections})
            self.memory.add(
                "strategy_roundtable",
                memo,
                strategy="strategy_agent",
                importance=0.78,
                confidence=0.75,
                source="strategy_roundtable",
                metadata={"date": today, "run_id": run_id, "sections": sections},
            )
        return memo

    def generate_daily_report(self, holdings: list[Holding], dry_run: bool = False) -> str:
        base_symbols = sorted(
            expand_leveraged_symbols(
                research_symbols(holdings)
                | set(self.config.research.default_universe)
                | set(self.config.research.leveraged_etfs)
                | set(self.config.research.comparison_etfs)
            )
        )
        if self.config.strategy_lab.daily_factor_iteration_enabled:
            factor_iteration = self.iterate_strategy_factors(dry_run=dry_run)
        else:
            factor_iteration = None
        scout = self.scout_strategy_news(base_symbols, dry_run=dry_run, deep=True, allow_external=False)
        graph_related = self.relation_graph.related_symbols(base_symbols, min_confidence=self.config.relation_graph.min_confidence) if self.relation_graph else set()
        graph_relationships = self.relation_graph.relationships_for(set(base_symbols) | graph_related, min_confidence=self.config.relation_graph.min_confidence) if self.relation_graph else []
        symbols = sorted(expand_leveraged_symbols(set(base_symbols) | set(scout.related_symbols()) | graph_related))
        quotes = self.data_hub.quotes(symbols, commit=not dry_run)
        if self.bar_store:
            self.bar_store.refresh_from_quotes(quotes, commit=not dry_run)
        digest = self.generate_daily_digest(symbols, days=5, dry_run=dry_run, include_web=False)
        all_relationships = [*scout.relationships, *stored_relations_to_scout(graph_relationships)]
        news = bridge_related_news(dedupe_news_items([*digest.items, *scout.news_items]), all_relationships, symbols)
        ranked_evidence = self.evidence_ranker.rank_news(news, symbols, commit=not dry_run)
        news = self.evidence_ranker.top_news_items(news, symbols, commit=False)
        feature_map = self.features.compute_many(symbols, quotes, news, holdings=holdings, commit=not dry_run)
        scores = self.score_universe(symbols, quotes, news, feature_map, holdings=holdings, commit=not dry_run)
        options = self.long_call_candidates(symbols, quotes, news)
        analyzed_news = analyze_news_items(news, holdings, set(symbols), limit=15, min_relevance=self.config.research.min_news_relevance)
        news_section = ranked_evidence_summary(ranked_evidence) + "\n\n" + format_analyzed_news_section(analyzed_news)
        company_detail_section = self.company_detail_section(scores, news, quotes, feature_map, holdings)
        option_section = "\n".join(candidate.reason for candidate in options[:12]) or "未配置期权数据，或没有长期 call 候选满足筛选条件。"
        memory_context = self.memory.context(
            "semiconductor reversal catalyst long call valuation risk",
            symbols,
            limit=self.config.memory.max_context_items,
        )
        paper_summary = self.paper.review()
        skill_summary = self.strategy_skill_summary(symbols)
        backtest_summary = self.backtest_summary()
        agent_summary = self.agent_status_summary()
        strategy_scout_section = scout.summary()
        relation_summary = graph_relationship_summary(graph_relationships)
        if relation_summary:
            strategy_scout_section += "\n\n" + relation_summary
        if factor_iteration is not None:
            strategy_scout_section += "\n\n" + factor_iteration.summary
        if dry_run or not self.openai.configured:
            report = self._fallback_report(scores, news_section, company_detail_section, option_section, memory_context, holdings, paper_summary, skill_summary, backtest_summary, agent_summary, strategy_scout_section)
            report = self._verify_report_or_fallback(report, holdings, graph_relationships, scout.queries, symbols, dry_run=dry_run)
            self._remember_report(report, symbols)
            return report
        try:
            report = self.openai.generate_report(
                "半导体与持仓机会日报",
                holdings,
                scores,
                news,
                memory_context,
                paper_summary,
                skill_summary,
                backtest_summary,
                agent_summary,
            )
            full_report = report + "\n\n## 策略自主深挖\n" + strategy_scout_section + "\n\n## 候选公司详解\n" + company_detail_section + "\n\n长期期权候选:\n" + option_section + "\n\n## Agent 运行状态\n" + agent_summary
            full_report = self._verify_report_or_fallback(full_report, holdings, graph_relationships, scout.queries, symbols, dry_run=dry_run)
            self._remember_report(full_report, symbols)
            return full_report
        except Exception as exc:
            fallback = self._fallback_report(scores, news_section, company_detail_section, option_section, memory_context, holdings, paper_summary, skill_summary, backtest_summary, agent_summary, strategy_scout_section)
            full_report = f"{fallback}\n\nLLM 报告生成失败，已使用本地中文回退报告。错误: {exc}"
            full_report = self._verify_report_or_fallback(full_report, holdings, graph_relationships, scout.queries, symbols, dry_run=dry_run)
            self._remember_report(full_report, symbols)
            return full_report

    def generate_research_brief(
        self,
        symbols: list[str] | None = None,
        *,
        holdings: list[Holding] | None = None,
        days: int = 3,
        dry_run: bool = False,
        force_refresh: bool = False,
    ) -> str:
        holdings = holdings or []
        symbol_set = {_normalize_research_symbol(symbol) for symbol in symbols or [] if symbol}
        if not symbol_set:
            symbol_set = research_symbols(holdings) | set(self.config.research.default_universe)
        symbol_set = expand_leveraged_symbols(symbol_set)
        normalized = sorted(symbol for symbol in symbol_set if symbol)
        quotes = self.data_hub.quotes(normalized, commit=not dry_run)
        if self.bar_store:
            self.bar_store.refresh_from_quotes(quotes, commit=not dry_run)
        if force_refresh:
            news = self.data_hub.collect_news(normalized, days=days, commit=not dry_run, force_refresh=True)
        else:
            news = self.collect_news(normalized, days=days, commit=not dry_run)
        news = self.evidence_ranker.top_news_items(news, normalized, commit=not dry_run)
        feature_map = self.features.compute_many(normalized, quotes, news, holdings=holdings, commit=not dry_run)
        scores = self.score_universe(normalized, quotes, news, feature_map, holdings=holdings, commit=not dry_run)
        analyzed_news = analyze_news_items(news, holdings, set(normalized), limit=20, min_relevance=self.config.research.min_news_relevance)
        news_section = format_analyzed_news_section(analyzed_news)
        company_detail = self.company_detail_section(scores, news, quotes, feature_map, holdings, limit=min(12, max(1, len(normalized))))
        score_lines = "\n".join(
            f"- {score.symbol}: 分数 {score.score:.1f}, 动作={action_for_score(score.score)}, 置信度 {score.confidence:.2f}; 催化: {', '.join(score.catalysts[:4]) or '暂无'}; 风险: {', '.join(score.risk_flags[:4]) or '暂无明确风险'}"
            for score in scores[:15]
        )
        brief = (
            "主动新闻整理与公司分析\n\n"
            f"## 覆盖范围\n{', '.join(normalized) or '暂无 symbol'}；新闻窗口 {max(1, days)} 天。\n\n"
            "## 新闻整理\n"
            f"{news_section}\n\n"
            "## 候选公司详解\n"
            f"{company_detail or '暂无公司详解。'}\n\n"
            "## 策略评分摘要\n"
            f"{score_lines or '暂无策略评分。'}\n\n"
            "## 使用边界\n"
            "这是主动研究触发结果，不是真实交易指令；需要结合公告原文、成交量、盘口和风险限额复核。\n"
        )
        if not dry_run:
            self.memory.add(
                "research_brief",
                "\n".join(brief.splitlines()[:24]),
                symbol=",".join(normalized[:12]),
                strategy="active_research",
                importance=0.72,
                confidence=0.65,
                source="research_now",
                metadata={"symbols": normalized, "days": days, "news_count": len(news), "force_refresh": force_refresh},
            )
        return brief

    def company_detail_section(
        self,
        scores: list[StrategyScore],
        news: list[NewsItem],
        quotes: dict[str, Quote | None],
        feature_map: dict[str, dict],
        holdings: list[Holding],
        *,
        limit: int = 10,
    ) -> str:
        best_scores: dict[str, StrategyScore] = {}
        for score in scores:
            symbol = score.symbol.upper()
            current = best_scores.get(symbol)
            if current is None or score.score > current.score:
                best_scores[symbol] = score
        holding_exposure = holding_exposure_by_symbol(holdings)
        ordered_symbols = [score.symbol.upper() for score in sorted(best_scores.values(), key=lambda item: item.score, reverse=True)]
        for symbol in sorted(holding_exposure):
            if symbol not in ordered_symbols:
                ordered_symbols.append(symbol)
        by_symbol = news_by_symbol(news, ordered_symbols)
        lines: list[str] = []
        for symbol in ordered_symbols[: max(1, limit)]:
            score = best_scores.get(symbol)
            quote = quotes.get(symbol) or quotes.get(symbol.upper())
            features = feature_map.get(symbol, {})
            symbol_news = by_symbol.get(symbol, [])
            top_news = "; ".join(readable_news_evidence(item) for item in symbol_news[: self.config.research.max_news_per_symbol]) if symbol_news else "暂无直接新闻"
            quote_text = format_quote_for_detail(quote)
            feature_text = format_feature_for_detail(features)
            exposure_text = holding_exposure.get(symbol, "未直接持有，仅研究池观察")
            action = action_for_score(score.score) if score else "review_required"
            if score:
                lines.append(
                    f"- {symbol}: 评分 {score.score:.1f}, 动作={action}, 置信度 {score.confidence:.2f}; "
                    f"行情: {quote_text}; 关键新闻: {top_news}; "
                    f"特征: {feature_text}; 持仓: {exposure_text}; "
                    f"多头理由: {score.bull_case}; 主要风险: {score.bear_case or ', '.join(score.risk_flags) or '暂无明确风险'}"
                )
            else:
                lines.append(
                    f"- {symbol}: 暂无策略评分, 动作={action}; 行情: {quote_text}; "
                    f"关键新闻: {top_news}; 特征: {feature_text}; 持仓: {exposure_text}"
                )
        return "\n".join(lines)

    @staticmethod
    def _fallback_report(
        scores: list[StrategyScore],
        news_section: str,
        company_detail_section: str,
        option_section: str,
        memory_context: str = "",
        holdings: list[Holding] | None = None,
        paper_summary: str = "",
        skill_summary: str = "",
        backtest_summary: str = "",
        agent_summary: str = "",
        strategy_scout_section: str = "",
    ) -> str:
        score_lines = "\n".join(
            f"- {score.symbol}: 分数 {score.score:.1f}, 置信度 {score.confidence:.2f}; 多头理由: {score.bull_case}; 风险: {', '.join(score.risk_flags) or '暂无明确风险标记'}"
            for score in scores[:15]
        )
        holding_lines = "\n".join(format_holding(h) for h in (holdings or []))
        return (
            "半导体与持仓机会日报\n\n"
            "## 今日组合概览\n"
            f"{holding_lines or '暂无真实持仓文件，无法汇总真实持仓。'}\n\n"
            "## 大行情和重大新闻\n"
            f"{news_section or '暂无新新闻或 X 线索；如果未配置 Finnhub/X API，这里会为空。'}\n\n"
            "## 当前真实持仓观察\n"
            "真实持仓与模拟持仓分开展示；本报告只做研究候选，不生成真实交易指令。\n\n"
            "## 模拟组合净值和盈亏\n"
            f"{paper_summary or '暂无模拟组合数据。'}\n\n"
            "## 策略 Skill 表现\n"
            f"{skill_summary or '暂无策略 Skill 状态。'}\n\n"
            "## Agent 运行状态\n"
            f"{agent_summary or '暂无 Agent 运行记录。'}\n\n"
            "## 策略自主深挖\n"
            f"{strategy_scout_section or '策略自主深挖未运行；报告不能直接下结论为待查证。'}\n\n"
            "## 候选公司详解\n"
            f"{company_detail_section or '暂无候选公司详解。'}\n\n"
            "## 长期期权候选\n"
            f"{option_section}\n\n"
            "## 半导体链路机会\n"
            f"{score_lines or '暂无策略评分；请检查行情/新闻数据源配置。'}\n\n"
            "## 最近回测/复盘结论\n"
            f"{backtest_summary or '暂无回测记录；可运行 backtest 命令生成。'}\n\n"
            "## 今日复盘和明日关注\n"
            f"{memory_context or '暂无可引用的历史记忆。'}\n\n"
            "## 新策略候选/是否需要创建新 Skill\n"
            "新策略先进入 candidate；每日 code iteration 只有在 py_compile、pytest、策略 dry-run 和回测门槛全部通过后才可自动升 active。真实交易边界不变。\n"
        )

    def _persist_factor_observations(self, scores: list[StrategyScore]) -> None:
        for score in scores:
            for row in score.metadata.get("factor_breakdown", []) or []:
                name = str(row.get("name", ""))
                if not name:
                    continue
                self._remember_once_today(
                    "factor_observation",
                    f"{score.symbol} {name}: value={row.get('value')}, contribution={row.get('contribution')}",
                    symbol=score.symbol,
                    strategy=score.strategy,
                    importance=min(0.9, max(0.35, abs(float(row.get("contribution") or 0.0)) / 20.0)),
                    confidence=score.confidence,
                    source="strategy_factor_mining",
                    metadata={"symbol": score.symbol, "strategy": score.strategy, "factor": row, "score": score.score},
                )

    def enqueue_paper_order_proposals(self, proposals: list[PaperOrderProposal]) -> list[dict[str, object]]:
        if not self.config.strategy_risk.auto_paper_orders_enabled:
            return [{"proposal_id": item.proposal_id, "status": "skipped", "reason": "auto_paper_orders_disabled"} for item in proposals]
        results: list[dict[str, object]] = []
        max_orders = max(0, int(self.config.strategy_risk.max_auto_paper_orders_per_day))
        cooldown = timedelta(hours=max(1, int(self.config.strategy_risk.paper_order_cooldown_hours)))
        for proposal in proposals:
            if len([row for row in results if row.get("status") in {"queued", "exists"}]) >= max_orders:
                results.append({"proposal_id": proposal.proposal_id, "symbol": proposal.symbol, "status": "skipped", "reason": "daily_auto_order_limit"})
                continue
            if not self.runtime.check_and_touch_cooldown(f"auto_paper_order:{proposal.symbol}:{proposal.side}", cooldown, commit=True):
                results.append({"proposal_id": proposal.proposal_id, "symbol": proposal.symbol, "status": "skipped", "reason": "symbol_cooldown"})
                continue
            if not self.runtime.check_and_touch_repeat_limit("auto_paper_orders", timedelta(hours=24), max_orders, commit=True):
                results.append({"proposal_id": proposal.proposal_id, "symbol": proposal.symbol, "status": "skipped", "reason": "daily_auto_order_limit"})
                continue
            job_type = "paper_buy" if proposal.side == "buy" else "paper_sell"
            payload = {
                "symbol": proposal.symbol,
                "asset_type": proposal.asset_type,
                "quantity": proposal.quantity,
                "price": proposal.price,
                "strategy_name": proposal.strategy_name,
                "strategy_version": proposal.strategy_version,
                "signal_id": proposal.signal_id,
                "reason": proposal.reason,
                "memory_context": json.dumps({"proposal_id": proposal.proposal_id, "risk_gate": asdict(proposal.risk_gate)}, ensure_ascii=False, default=str)[:2000],
            }
            job_id = self.runtime.enqueue_job(job_type, payload, priority=25, idempotency_key=f"paper_order:{proposal.proposal_id}")
            status = "queued" if job_id else "exists"
            proposal.metadata["execution"] = status
            proposal.metadata["paper_order_job_id"] = job_id
            result = {"proposal_id": proposal.proposal_id, "symbol": proposal.symbol, "job_id": job_id, "status": status, "job_type": job_type}
            results.append(result)
            self.memory.add(
                "paper_order_job",
                f"auto paper order {status}: {proposal.side} {proposal.symbol} {proposal.quantity:g} @ {proposal.price:g}",
                symbol=proposal.symbol,
                strategy=proposal.strategy_name,
                importance=0.78,
                confidence=0.75,
                source="strategy_plan",
                metadata={**result, "proposal": asdict(proposal)},
            )
        return results

    def _persist_strategy_signal(self, signal: StrategySignal, verdict: RiskGateVerdict, proposal: PaperOrderProposal | None) -> None:
        self._remember_once_today(
            "strategy_signal",
            f"策略信号 {signal.symbol}: 动作={signal.action}, 分数={signal.score:.1f}, 置信度={signal.confidence:.2f}, gate={verdict.severity}",
            symbol=signal.symbol,
            strategy=signal.strategy_name,
            importance=min(1.0, max(0.25, signal.score / 100.0)),
            confidence=signal.confidence,
            source="strategy_plan",
            metadata=asdict(signal),
            evidence_links=list(signal.metadata.get("evidence_links", []) or []),
        )
        self._remember_once_today(
            "risk_gate_verdict",
            f"风险门 {signal.symbol}: {'通过' if verdict.allowed else '阻断'} severity={verdict.severity}; {', '.join(verdict.reasons[:4])}",
            symbol=signal.symbol,
            strategy=signal.strategy_name,
            importance=0.8 if not verdict.allowed else 0.65,
            confidence=0.85,
            source="strategy_risk_gate",
            metadata=asdict(verdict),
        )
        if proposal:
            self._remember_once_today(
                "paper_order_proposal",
                f"纸面订单提案 {proposal.symbol}: {proposal.side} {proposal.quantity:g} @ {proposal.price:g}, gross=${proposal.gross_value:.2f}",
                symbol=proposal.symbol,
                strategy=proposal.strategy_name,
                importance=0.78,
                confidence=0.75,
                source="strategy_plan",
                metadata=asdict(proposal),
            )

    def _verify_report_or_fallback(
        self,
        report: str,
        holdings: list[Holding],
        relationships: list[StoredSymbolRelation],
        query_log: list[str],
        symbols: list[str],
        *,
        dry_run: bool,
    ) -> str:
        verification = self.report_verifier.verify(report, holdings, relationships, query_log=query_log, commit=not dry_run)
        if verification.blocked:
            fallback = fallback_verified_report(report, verification)
            if not dry_run:
                self.memory.add(
                    "missed_signal_review",
                    f"report blocked and downgraded for {', '.join(symbols[:8])}: {verification.summary}",
                    symbol=",".join(symbols[:8]),
                    strategy="report_verifier",
                    importance=0.82,
                    confidence=0.85,
                    source="report_verifier",
                    metadata=verification.to_dict(),
                )
            return fallback
        return report

    def _remember_report(self, report: str, symbols: list[str]) -> None:
        summary = "\n".join(report.splitlines()[:18])
        self.memory.add(
            "daily_review",
            summary,
            symbol=",".join(symbols[:12]),
            strategy="semiconductor_reversal",
            importance=0.8,
            confidence=0.6,
            source="report",
            metadata={"symbols": symbols[:50]},
        )
        self._append_strategy_review_memory(summary)

    def strategy_skill_summary(self, active_symbols: list[str] | None = None) -> str:
        infos = load_strategy_infos(self.config.strategy_root)
        recent_backtests = {result.strategy_name: result for result in self.backtests.recent(limit=20)}
        today = datetime.now(timezone.utc).date().isoformat()
        active_symbol_set = set(active_symbols or [])
        recent_orders = self.paper.orders(limit=200)
        positions = self.paper.positions()
        drawdown = paper_drawdown(self.paper)
        lines = []
        for info in infos:
            latest = recent_backtests.get(info.name)
            backtest_text = format_backtest_result(latest) if latest else "暂无回测记录"
            lesson = self.memory.recent(kind="strategy_lesson", strategy=info.name, limit=1)
            lesson_text = lesson[0].content if lesson else "暂无误判/经验记录"
            raw_signals_today = [
                record
                for kind in ("strategy_signal", "signal")
                for record in self.memory.recent(kind=kind, strategy=info.name, limit=300)
                if record.created_at.startswith(today)
                and (not active_symbol_set or record.symbol in active_symbol_set)
            ]
            signals_today = latest_unique_signals(raw_signals_today)
            failed_gates_today = [
                record
                for record in self.memory.recent(kind="risk_gate_verdict", strategy=info.name, limit=300)
                if record.created_at.startswith(today) and not bool((record.metadata or {}).get("allowed", False))
            ]
            proposals_today = [
                record
                for record in self.memory.recent(kind="paper_order_proposal", strategy=info.name, limit=300)
                if record.created_at.startswith(today)
            ]
            action_counts = {"paper_buy": 0, "paper_sell": 0, "watch": 0, "avoid": 0, "review_required": 0}
            for record in signals_today:
                action = str((record.metadata or {}).get("action", "review_required"))
                action_counts[action] = action_counts.get(action, 0) + 1
            orders_today = [order for order in recent_orders if order.strategy_name == info.name and order.created_at.startswith(today)]
            buy_count = sum(1 for order in orders_today if order.side == "buy")
            sell_count = sum(1 for order in orders_today if order.side == "sell")
            paper_pnl = sum(position.unrealized_pnl for position in positions if position.strategy_name == info.name)
            suggestion = skill_suggestion(info.status, latest, len(signals_today), paper_pnl)
            observe_count = action_counts.get("watch", 0) + action_counts.get("review_required", 0)
            lines.append(
                f"- {info.name} v{info.version}: 当前状态={info.status}; 今日信号数量={len(signals_today)}; "
                f"模拟买入/卖出/观察/回避数量={buy_count}/{sell_count}/{observe_count}/{action_counts.get('avoid', 0)}; "
                f"纸面提案={len(proposals_today)}; 失败风险门={len(failed_gates_today)}; "
                f"模拟 PnL=${paper_pnl:.2f}; 纸面回撤={drawdown:.2%}; 最近回测={backtest_text}; 最近误判原因={lesson_text}; 建议={suggestion}"
            )
        return "\n".join(lines)

    def backtest_summary(self) -> str:
        results = self.backtests.recent(limit=6)
        if not results:
            return "暂无回测记录。"
        return "\n".join(f"- {format_backtest_result(result)}" for result in results)

    def agent_status_summary(self) -> str:
        runs = self.runtime.recent_agent_runs(limit=8)
        if not runs:
            return "暂无 Agent 运行记录；maintenance/strategy/report agent 启动后会在这里沉淀 trace 和复盘。"
        failed = [row for row in runs if row.get("status") != "done"]
        lines = [
            f"- 最近 Agent runs: {len(runs)}",
            f"- 失败或未完成: {len(failed)}",
        ]
        for row in runs[:6]:
            lines.append(f"- {row.get('agent_name')}: {row.get('status')} - {row.get('objective')}")
        if failed:
            lines.append("- 建议: 优先查看 `portfolio-bot agent-review RUN_ID` 和 `agent-trace RUN_ID`。")
        else:
            lines.append("- 建议: 继续让 strategy/research/maintenance agent 每日留下 trace，方便长期复盘。")
        return "\n".join(lines)

    def _append_strategy_review_memory(self, report_summary: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        for info in load_strategy_infos(self.config.strategy_root):
            review_file = self.config.strategy_root / info.name / "review_memory.jsonl"
            review_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "created_at": now,
                "kind": "daily_review",
                "strategy_name": info.name,
                "strategy_version": info.version,
                "status": info.status,
                "summary": report_summary[:2000],
            }
            with review_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _remember_once_today(self, kind: str, content: str, **kwargs) -> None:
        symbol = str(kwargs.get("symbol", ""))
        strategy = str(kwargs.get("strategy", ""))
        today = datetime.now(timezone.utc).date().isoformat()
        for record in self.memory.recent(symbol=symbol, kind=kind, strategy=strategy, limit=20):
            if record.created_at.startswith(today) and record.content == content.strip():
                return
        self.memory.add(kind, content, **kwargs)


def format_holding(holding: Holding) -> str:
    if holding.asset_type == "option":
        meta = holding.metadata
        return (
            f"- 期权 {holding.name or holding.symbol}: {holding.quantity:g} 张, "
            f"底层 {meta.get('underlying', '未知')}, 到期 {meta.get('expiration', '未知')}, "
                f"行权价 {meta.get('strike', '未知')}, 截图市值 {holding.market_value if holding.market_value is not None else '未知'}"
        )
    exposure = leveraged_exposure(holding.normalized_symbol())
    exposure_text = f", 杠杆暴露 {exposure.chinese_label}" if exposure else ""
    return f"- {holding.asset_type} {holding.symbol}: 数量 {holding.quantity:g}, 截图市值 {holding.market_value if holding.market_value is not None else '未知'}{exposure_text}"


def research_symbols(holdings: list[Holding]) -> set[str]:
    symbols: set[str] = set()
    for holding in holdings:
        if holding.asset_type == "option":
            underlying = str(holding.metadata.get("underlying", "")).strip().upper()
            if underlying:
                symbols.add(underlying)
        elif holding.asset_type in {"equity", "etf"}:
            symbol = holding.normalized_symbol()
            symbols.add(symbol)
            exposure = leveraged_exposure(symbol)
            if exposure:
                symbols.add(exposure.underlying)
    return symbols


def direct_research_symbols(holdings: list[Holding]) -> set[str]:
    symbols: set[str] = set()
    for holding in holdings:
        if holding.asset_type == "option":
            underlying = str(holding.metadata.get("underlying", "")).strip().upper()
            if underlying:
                symbols.add(underlying)
        elif holding.asset_type in {"equity", "etf"}:
            symbols.add(holding.normalized_symbol())
    return symbols


def _normalize_research_symbol(value: str) -> str:
    return value.strip().upper()


def holding_exposure_by_symbol(holdings: list[Holding]) -> dict[str, str]:
    exposure: dict[str, list[str]] = {}
    for holding in holdings:
        if holding.asset_type == "option":
            symbol = str(holding.metadata.get("underlying", "")).strip().upper()
            if not symbol:
                continue
            text = f"期权 {holding.name or holding.symbol} {holding.quantity:g} 张"
        else:
            symbol = holding.normalized_symbol()
            text = f"{holding.asset_type} {holding.quantity:g} 股/份"
        if holding.market_value is not None:
            text += f", 市值 ${holding.market_value:.2f}"
        leveraged = leveraged_exposure(symbol)
        if leveraged:
            text += f", 杠杆暴露 {leveraged.chinese_label}"
        exposure.setdefault(symbol, []).append(text)
        if leveraged:
            underlying_text = f"{holding.symbol} {holding.quantity:g} 股/份，{leveraged.chinese_label}"
            if holding.market_value is not None:
                underlying_text += f"，约 ${holding.market_value * leveraged.multiplier:.2f} 名义暴露"
            exposure.setdefault(leveraged.underlying, []).append(underlying_text)
    return {symbol: "；".join(items[:3]) for symbol, items in exposure.items()}


def exposure_value_by_symbol(holdings: list[Holding]) -> dict[str, float]:
    values: dict[str, float] = {}
    for holding in holdings:
        if holding.asset_type == "option":
            symbol = str(holding.metadata.get("underlying", "")).strip().upper()
        else:
            symbol = holding.normalized_symbol()
        if not symbol:
            continue
        value = float(holding.market_value or 0.0)
        values[symbol] = values.get(symbol, 0.0) + value
        leveraged = leveraged_exposure(symbol)
        if leveraged:
            values[leveraged.underlying] = values.get(leveraged.underlying, 0.0) + value * leveraged.multiplier
    return values


def paper_exposure_by_symbol(positions) -> dict[str, float]:
    values: dict[str, float] = {}
    for position in positions:
        symbol = str(position.symbol).upper()
        value = float(position.market_value)
        values[symbol] = values.get(symbol, 0.0) + value
        leveraged = leveraged_exposure(symbol)
        if leveraged:
            values[leveraged.underlying] = values.get(leveraged.underlying, 0.0) + value * leveraged.multiplier
    return values


def merged_exposure_value(values: dict[str, float], symbol: str, relationships: list[StoredSymbolRelation] | None = None) -> float:
    symbol = str(symbol or "").strip().upper()
    exposure = leveraged_exposure(symbol)
    if exposure:
        return max(float(values.get(symbol, 0.0)), float(values.get(exposure.underlying, 0.0)))
    direct = float(values.get(symbol, 0.0))
    total = direct
    for leveraged_symbol, leveraged in LEVERAGED_EXPOSURES.items():
        if leveraged.underlying == symbol:
            total = max(total, float(values.get(leveraged_symbol, 0.0)), float(values.get(symbol, 0.0)))
    for relation in relationships or []:
        if relation.confidence < 0.55:
            continue
        if relation.source_symbol == symbol:
            total = max(total, direct, float(values.get(relation.related_symbol, 0.0)))
        elif relation.related_symbol == symbol:
            total = max(total, direct, float(values.get(relation.source_symbol, 0.0)) * max(1.0, float(relation.multiplier or 1.0)))
    return total


def stored_relations_to_scout(relations: list[StoredSymbolRelation]) -> list[SymbolRelationship]:
    result: list[SymbolRelationship] = []
    for relation in relations:
        result.append(
            SymbolRelationship(
                source_symbol=relation.source_symbol,
                related_symbol=relation.related_symbol,
                relation_type=relation.relation_type,
                confidence=relation.confidence,
                evidence_title=relation.evidence_title,
                evidence_url=relation.evidence_url,
                query=str(relation.metadata.get("query") or relation.source or "relation_graph"),
                metadata={
                    **dict(relation.metadata or {}),
                    "source": relation.source,
                    "multiplier": relation.multiplier,
                    "observed_at": relation.observed_at.isoformat(),
                },
                created_at=relation.observed_at,
            )
        )
    return result


def graph_relationship_summary(relations: list[StoredSymbolRelation]) -> str:
    if not relations:
        return ""
    lines = ["底层关系图"]
    for relation in sorted(relations, key=lambda item: item.confidence, reverse=True)[:10]:
        multiplier = f"{relation.multiplier:g}x " if relation.multiplier and relation.multiplier != 1.0 else ""
        evidence = f" evidence={relation.evidence_url}" if relation.evidence_url else ""
        lines.append(
            f"- {relation.source_symbol}: 直接持仓/产品；经济底层 {multiplier}{relation.related_symbol}; "
            f"type={relation.relation_type}; confidence={relation.confidence:.2f}; source={relation.source}{evidence}"
        )
    return "\n".join(lines)


def event_matches_signal(event_symbol: str, signal_symbol: str) -> bool:
    event_symbol = str(event_symbol or "").strip().upper()
    signal_symbol = str(signal_symbol or "").strip().upper()
    if event_symbol == signal_symbol:
        return True
    exposure = leveraged_exposure(signal_symbol)
    if exposure and exposure.underlying == event_symbol:
        return True
    inverse = leveraged_exposure(event_symbol)
    return bool(inverse and inverse.underlying == signal_symbol)


def current_paper_equity(paper: PaperBroker) -> float:
    return float(paper.cash()) + sum(float(position.market_value) for position in paper.positions())


def paper_drawdown(paper: PaperBroker, *, current_equity: float | None = None) -> float:
    current = current_paper_equity(paper) if current_equity is None else float(current_equity)
    rows = paper.equity_curve(limit=200)
    values = [current]
    for row in rows:
        try:
            values.append(float(row.get("equity") or 0.0))
        except (TypeError, ValueError):
            continue
    peak = max([float(paper.starting_cash), *values])
    if peak <= 0:
        return 0.0
    return min(0.0, (current - peak) / peak)


def strategy_signal_from_score(score: StrategyScore, *, strategy_version: str) -> StrategySignal:
    action = action_for_score(score.score)
    date_key = datetime.now(timezone.utc).date().isoformat()
    signal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{date_key}:{score.strategy}:{score.symbol}:{score.score:.3f}:{score.confidence:.3f}"))
    return StrategySignal(
        signal_id=signal_id,
        symbol=score.symbol.upper(),
        strategy_name=score.strategy,
        strategy_version=strategy_version,
        action=action,  # type: ignore[arg-type]
        score=score.score,
        confidence=score.confidence,
        reason=score.bull_case,
        metadata={
            "bear_case": score.bear_case,
            "catalysts": score.catalysts,
            "valuation_gap": score.valuation_gap,
            "option_quality": score.option_quality,
        },
    )


def paper_order_proposal_from_signal(signal: StrategySignal, verdict: RiskGateVerdict, quote: Quote | None) -> PaperOrderProposal | None:
    if not verdict.allowed or signal.action != "paper_buy" or not quote or quote.price <= 0:
        return None
    quantity = round(verdict.max_notional / quote.price, 4)
    if quantity <= 0:
        return None
    proposal_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"paper_order_proposal:{signal.signal_id}:{verdict.max_notional:.2f}"))
    gross = quantity * quote.price
    return PaperOrderProposal(
        proposal_id=proposal_id,
        signal_id=signal.signal_id,
        symbol=signal.symbol,
        asset_type="equity",
        side="buy",
        quantity=quantity,
        price=quote.price,
        gross_value=gross,
        strategy_name=signal.strategy_name,
        strategy_version=signal.strategy_version,
        reason=signal.reason,
        risk_gate=verdict,
        metadata={"paper_only": True, "execution": "proposal_only"},
    )


def render_strategy_plan_summary(
    signals: list[StrategySignal],
    verdicts: list[RiskGateVerdict],
    proposals: list[PaperOrderProposal],
    drawdown: float,
    *,
    paper_order_jobs: list[dict[str, object]] | None = None,
) -> str:
    passed = sum(1 for verdict in verdicts if verdict.allowed)
    blocked = len(verdicts) - passed
    lines = [
        "策略计划",
        f"- 信号: {len(signals)}; 风险门通过/阻断: {passed}/{blocked}; 纸面订单提案: {len(proposals)}; 纸面回撤: {drawdown:.2%}",
    ]
    for signal, verdict in list(zip(signals, verdicts))[:15]:
        lines.append(
            f"- {signal.symbol}: action={signal.action}, score={signal.score:.1f}, confidence={signal.confidence:.2f}, "
            f"gate={verdict.severity}, max_notional=${verdict.max_notional:.2f}, reasons={'; '.join(verdict.reasons[:3])}"
        )
    if proposals:
        queued = sum(1 for row in (paper_order_jobs or []) if row.get("status") in {"queued", "exists"})
        if paper_order_jobs is None:
            lines.append("纸面提案已生成；非 dry-run 且自动 paper 开启时会进入 paper worker 队列。")
        else:
            lines.append(f"纸面自动执行: queued/existing={queued}, skipped={len(paper_order_jobs) - queued}; 真实交易仍禁用。")
    return "\n".join(lines)


def roundtable_source_view(config: BotConfig, recent_digest) -> str:
    digest_text = recent_digest[0].content[:500] if recent_digest else "暂无 daily_news_summary。"
    provider = config.research.web_search_api_provider if config.research.web_search_enabled else "disabled"
    return f"当前 web_search_enabled={config.research.web_search_enabled}, provider={provider}。最近 digest: {digest_text}"


def roundtable_risk_view(recent_gates, paper: PaperBroker, starting_cash: float) -> str:
    failed = [record for record in recent_gates if not bool((record.metadata or {}).get("allowed", False))]
    drawdown = paper_drawdown(paper)
    return f"纸面账户起始现金 ${starting_cash:.2f}，当前回撤 {drawdown:.2%}。最近风险门 {len(recent_gates)} 条，失败 {len(failed)} 条。"


def roundtable_report_view(recent_reviews, recent_digest) -> str:
    if not recent_reviews:
        return "暂无日报复盘记忆；报告 agent 应优先确认 digest、signal、gate、proposal 都进入日报。"
    digest_status = "已有 digest" if recent_digest else "缺少 digest"
    return f"{digest_status}；最近日报/复盘 {len(recent_reviews)} 条。继续要求报告分清真实持仓、纸面组合和订单提案。"


def roundtable_strategy_view(recent_gates, recent_proposals) -> str:
    failed = [record for record in recent_gates if not bool((record.metadata or {}).get("allowed", False))]
    if failed:
        return f"今日优先处理 {len(failed)} 个失败风险门，暂不扩大自动化。纸面提案 {len(recent_proposals)} 个，仍为 proposal-only。"
    return f"风险门无明显失败记录；纸面提案 {len(recent_proposals)} 个，继续保持 proposal-only 并补充回测/证据。"


def news_by_symbol(news: list[NewsItem], symbols: list[str]) -> dict[str, list[NewsItem]]:
    result: dict[str, list[NewsItem]] = {symbol.upper(): [] for symbol in symbols}
    known_symbols = set(result)
    for item in news:
        item_symbols = {value.upper() for value in item.symbols if value}
        candidate_symbols = item_symbols & known_symbols
        if not candidate_symbols:
            text = f"{item.title} {item.summary}"
            candidate_symbols = {symbol for symbol in known_symbols if symbol_matches_text(symbol, text)}
        for symbol in candidate_symbols:
            if news_relevance(item, symbol) >= 0.45:
                result[symbol].append(item)
    for items in result.values():
        items.sort(key=lambda item: item.published_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    return result


def bridge_related_news(news: list[NewsItem], relationships: list[SymbolRelationship], target_symbols: list[str]) -> list[NewsItem]:
    if not relationships:
        return news
    target_set = {symbol.upper() for symbol in target_symbols}
    bridged: list[NewsItem] = list(news)
    seen = {item.dedupe_key() for item in news}
    for relation in relationships:
        source = relation.source_symbol.upper()
        related = relation.related_symbol.upper()
        if source not in target_set or relation.confidence < 0.55:
            continue
        for item in news:
            item_symbols = {symbol.upper() for symbol in item.symbols if symbol}
            text = f"{item.title} {item.summary}"
            if related not in item_symbols and not symbol_matches_text(related, text):
                continue
            raw = dict(item.raw or {})
            raw["relationship"] = relation.to_dict()
            symbols = sorted({*item_symbols, source, related})
            bridged_item = NewsItem(
                title=item.title,
                url=item.url,
                source=item.source,
                published_at=item.published_at,
                symbols=symbols,
                summary=(item.summary + f" Related to {source} through {relation.related_symbol} ({relation.relation_type}).").strip(),
                kind=item.kind,
                raw=raw,
            )
            key = f"bridge:{source}:{related}:{bridged_item.dedupe_key()}"
            if key in seen:
                continue
            seen.add(key)
            bridged.append(bridged_item)
    return bridged


def format_quote_for_detail(quote: Quote | None) -> str:
    if not quote:
        return "暂无报价"
    change = "未知涨跌幅" if quote.change_percent is None else f"{quote.change_percent:+.2f}%"
    return f"${quote.price:.2f}, 日内 {change}"


def format_feature_for_detail(features: dict) -> str:
    if not features:
        return "暂无特征"
    sentiment = float(features.get("sentiment_score") or 0.0)
    high_impact = int(features.get("high_impact_news_count") or 0)
    chain_hits = features.get("chain_hits") or []
    chains = ",".join(chain_hits[:3]) if isinstance(chain_hits, list) and chain_hits else "无明确链路"
    relevance = float(features.get("relevance_score") or 0.0)
    behavior_score = float(features.get("daily_behavior_score") or 0.0)
    intraday = float(features.get("intraday_return_pct") or 0.0)
    ret5 = float(features.get("return_5d") or 0.0)
    rel_volume = float(features.get("relative_volume") or 0.0)
    behavior_flags = features.get("behavior_flags") or []
    behavior_text = ",".join(behavior_flags[:3]) if isinstance(behavior_flags, list) and behavior_flags else "无"
    return f"情绪 {sentiment:+.0f}, 高影响新闻 {high_impact}, 相关新闻相关性 {relevance:.2f}, 链路 {chains}, 日常行为 {behavior_score:+.1f}(日内 {intraday:+.2f}%, 5日 {ret5:+.2f}%, RVOL {rel_volume:.2f}, {behavior_text})"


def readable_news_evidence(item: NewsItem) -> str:
    published = item.published_at.date().isoformat() if item.published_at else "未知日期"
    source = item.source or "未知来源"
    via = " via Finnhub" if "finnhub.io/api/news" in (item.url or "").lower() else ""
    summary = " ".join((item.summary or item.title).split())
    if len(summary) > 180:
        summary = summary[:177].rstrip() + "..."
    return f"{published} {source}{via}: {item.title} - {summary}"


def ranked_evidence_summary(ranked) -> str:
    if not ranked:
        return "证据排序：暂无可展示的高相关证据。"
    lines = ["证据排序 Top Evidence"]
    for item in sorted(ranked, key=lambda row: row.score, reverse=True)[:20]:
        published = item.published_at.date().isoformat() if item.published_at else "未知日期"
        reasons = ",".join(item.reasons[:3]) if item.reasons else "ranked"
        lines.append(f"- {item.symbol}: score={item.score:.2f}; {published} {item.source}: {item.title} {item.url} ({reasons})")
    return "\n".join(lines)


def latest_unique_signals(records) -> list:
    by_key = {}
    for record in records:
        action = str((record.metadata or {}).get("action", "review_required"))
        by_key[(record.symbol, action)] = record
    return list(by_key.values())


def action_for_score(score: float) -> str:
    if score >= 70:
        return "paper_buy"
    if score >= 50:
        return "watch"
    if score <= 25:
        return "avoid"
    return "review_required"


def signal_text(symbol: str, score: StrategyScore) -> str:
    return (
        f"策略信号 {symbol}: 动作={action_for_score(score.score)}, 分数={score.score:.1f}, "
        f"置信度={score.confidence:.2f}, 理由={score.bull_case}, 风险={score.bear_case}"
    )


def skill_suggestion(status: str, latest_backtest, signal_count: int, paper_pnl: float) -> str:
    if status == "candidate":
        return "继续候选观察，只参与回测和模拟观察，满足 2-4 周纸面验证后再考虑 active"
    if status in {"paused", "retired"}:
        return "不产生新信号，除非人工复盘后重新启用"
    if latest_backtest is None:
        return "保持 active 但优先补回测，避免只靠主观判断扩大权重"
    if latest_backtest.trade_count < 8:
        return "回测交易数偏少，保持低权重观察"
    if latest_backtest.max_drawdown < -0.35:
        return "最大回撤偏大，建议降权或暂停新增模拟买入"
    if paper_pnl < -500:
        return "模拟亏损扩大，明日复盘误判来源"
    if signal_count == 0:
        return "今日无新信号，保持原状态"
    return "保持 active，继续观察模拟交易和新闻验证"

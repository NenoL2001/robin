from __future__ import annotations

import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from ..config import BotConfig
from .data_hub import DataHub
from ..data.finnhub import FinnhubClient
from ..memory import MemoryStore, memory_path
from ..models import Holding, MarketEvent, Quote
from ..notifications import AgentMailNotifier, CompositeNotifier, build_notifier
from ..openai_client import OpenAIService
from .research import ResearchEngine
from ..runtime import RuntimeStore, event_idempotency_key, runtime_path
from ..storage import Storage, load_holdings, quote_to_snapshot, snapshot_to_quote
from .exposures import leveraged_exposure
from .metrics import symbol_matches_text


LLM_ANALYSIS_BACKOFF_KEY = "llm:major_event_analysis:backoff"
LLM_ANALYSIS_BACKOFF = timedelta(minutes=30)
OPENAI_ANALYSIS_BACKOFF_KEY = LLM_ANALYSIS_BACKOFF_KEY
OPENAI_ANALYSIS_BACKOFF = LLM_ANALYSIS_BACKOFF


class PortfolioMonitor:
    def __init__(
        self,
        config: BotConfig,
        notifier: CompositeNotifier | None = None,
        finnhub: FinnhubClient | None = None,
        openai_service: OpenAIService | None = None,
        dry_run: bool = False,
    ):
        self.config = config
        self.storage = Storage(config.data_dir)
        self.notifier = notifier or build_notifier(config, dry_run=dry_run)
        self.finnhub = finnhub or FinnhubClient(config.finnhub_api_key)
        self.openai = openai_service or OpenAIService(config)
        self.research = ResearchEngine(config, finnhub=self.finnhub, openai_service=self.openai)
        self.memory = MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)
        self.runtime = RuntimeStore(runtime_path(config.data_dir, config.runtime.sqlite_path))
        self.data_hub = DataHub(config, runtime=self.runtime, finnhub=self.finnhub)
        self.dry_run = dry_run

    def run_forever(self) -> None:
        last_deep_scan = 0.0
        while True:
            if not network_available():
                print("network unavailable; waiting")
                time.sleep(30)
                continue
            try:
                self.scan_once(send_alerts=True)
                now = time.time()
                if now - last_deep_scan >= self.config.monitor.deep_scan_seconds:
                    self.deep_scan(send_report=False)
                    last_deep_scan = now
                self.maybe_send_daily_report()
            except Exception as exc:
                print(f"monitor error: {exc}")
            time.sleep(self.config.monitor.realtime_poll_seconds)

    def scan_once(self, send_alerts: bool = False) -> list[MarketEvent]:
        holdings = load_holdings(self.config.holdings_path)
        if not holdings:
            print(f"No holdings found at {self.config.holdings_path}")
            return []
        quotes = self.fetch_quotes(holdings)
        events = self.detect_events(holdings, quotes, commit=not self.dry_run)
        if send_alerts:
            batch_quick = self.config.notifications.batch_realtime_alerts and len(events) > 1
            for event in events:
                self.handle_event(event, send_major_email=False, send_quick_alert=not batch_quick)
            if batch_quick:
                self.send_realtime_event_batch(events)
            self.send_major_event_email_batch(events)
        return events

    def deep_scan(self, send_report: bool = False) -> str:
        holdings = load_holdings(self.config.holdings_path)
        alert_symbols = holding_alert_symbols(holdings)
        symbols = list(alert_symbols)
        symbols.extend(self.config.research.default_universe)
        news = self.research.collect_news(sorted(set(symbols)), commit=not self.dry_run)
        high_impact = select_high_impact_news(news, max_items=5, symbols=alert_symbols)
        events: list[MarketEvent] = []
        for item in high_impact:
            symbols = item.symbols or [symbol for symbol in self.config.research.default_universe if symbol in item.title.upper()]
            for symbol in symbols[:3]:
                events.append(
                    MarketEvent(
                        symbol=symbol,
                        event_type="high_impact_news",
                        severity="high",
                        message=f"High-impact lead for {symbol}: {item.title}",
                        news=item,
                    )
                )
        events = self._apply_cooldown(events, commit=not self.dry_run)
        if self.dry_run and events:
            for event in events:
                print(f"[dry-run:high-impact] {event.message}")
        elif events:
            for event in events:
                self.handle_event(event, send_major_email=False)
            self.send_major_event_email_batch(events)
        if send_report:
            report = self.research.generate_daily_report(holdings, dry_run=self.dry_run)
            self.notifier.send("Portfolio bot daily semiconductor report", report)
            return report
        return f"deep scan complete; fresh_news={len(news)} high_impact={len(high_impact)} alerts={len(events)}"

    def maybe_send_daily_report(self) -> bool:
        now = datetime.now(ZoneInfo(self.config.timezone))
        try:
            hour, minute = [int(part) for part in self.config.monitor.report_time.split(":", 1)]
        except ValueError:
            hour, minute = 8, 45
        if (now.hour, now.minute) < (hour, minute):
            return False
        today = now.date().isoformat()
        report_key = f"daily_report:{today}"
        if not self.runtime.check_and_touch_cooldown(report_key, timedelta(hours=20), commit=not self.dry_run):
            return False
        self.deep_scan(send_report=True)
        return True

    def fetch_quotes(self, holdings: list[Holding]) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        symbols = sorted(holding_alert_symbols(holdings))
        if not symbols:
            return quotes
        workers = max(1, min(self.config.rate_limits.finnhub_concurrency, len(symbols)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(self.data_hub.quote, symbol, commit=not self.dry_run): symbol for symbol in symbols}
            for future in as_completed(futures):
                quote = future.result()
                if quote:
                    quotes[quote.symbol] = quote
        return quotes

    def detect_events(self, holdings: list[Holding], quotes: dict[str, Quote], commit: bool = True) -> list[MarketEvent]:
        events: list[MarketEvent] = []
        portfolio_previous = 0.0
        portfolio_current = 0.0
        portfolio_comparable_current = 0.0
        portfolio_snapshot_count = 0
        for holding in holdings:
            symbol = holding.normalized_symbol() if holding.asset_type in {"equity", "etf"} else str(holding.metadata.get("underlying", "")).upper()
            if not symbol:
                continue
            quote = quotes.get(symbol)
            if not quote:
                continue
            old_quote = self.runtime.quote_snapshot(symbol)
            if old_quote:
                multiplier = float(holding.metadata.get("multiplier", 1) or 1) if holding.asset_type == "option" else 1.0
                pct = percent_change(old_quote.price, quote.price)
                if abs(pct) >= self.config.monitor.thresholds.realtime_percent:
                    events.append(
                        MarketEvent(
                            symbol=symbol,
                            event_type="realtime_move",
                            severity="high",
                            message=f"{symbol} moved {pct:+.2f}% since last snapshot ({old_quote.price:.2f} -> {quote.price:.2f})",
                            quote=quote,
                            metadata={"change_percent": pct},
                        )
                    )
                portfolio_previous += old_quote.price * holding.quantity * multiplier
                portfolio_comparable_current += quote.price * holding.quantity * multiplier
                portfolio_snapshot_count += 1
            if quote.change_percent is not None and abs(quote.change_percent) >= self.config.monitor.thresholds.intraday_percent:
                events.append(
                    MarketEvent(
                        symbol=symbol,
                        event_type="intraday_move",
                        severity="high",
                        message=f"{symbol} intraday move {quote.change_percent:+.2f}% at {quote.price:.2f}",
                        quote=quote,
                        metadata={"change_percent": quote.change_percent},
                    )
                )
            multiplier = float(holding.metadata.get("multiplier", 1) or 1) if holding.asset_type == "option" else 1.0
            portfolio_current += quote.price * holding.quantity * multiplier
            if commit:
                self.runtime.save_quote_snapshot(quote)
        if portfolio_previous > 0 and portfolio_snapshot_count >= max(1, min(2, len(quotes))):
            portfolio_pct = percent_change(portfolio_previous, portfolio_comparable_current)
            if abs(portfolio_pct) >= self.config.monitor.thresholds.portfolio_percent:
                events.append(
                    MarketEvent(
                        symbol="PORTFOLIO",
                        event_type="portfolio_move",
                        severity="high",
                        message=f"Portfolio estimated value moved {portfolio_pct:+.2f}% since last snapshot",
                        metadata={"change_percent": portfolio_pct},
                    )
                )
        return self._apply_cooldown(events, commit=commit)

    def handle_event(self, event: MarketEvent, *, send_major_email: bool = True, send_quick_alert: bool = True) -> None:
        subject = f"Portfolio alert: {event.symbol} {event.event_type}"
        body = event.message
        memory_context = self.memory.context(event.message, [event.symbol], limit=self.config.memory.max_context_items)
        key = event_idempotency_key(event)
        if not self.dry_run:
            self.runtime.add_market_event(event, idempotency_key=key)
            self.memory.add(
                "market_event",
                event.message,
                symbol=event.symbol,
                importance=0.9 if event.severity == "high" else 0.5,
                confidence=0.7,
                source="monitor",
                metadata={"event_type": event.event_type, "severity": event.severity, "idempotency_key": key},
            )
        if send_quick_alert:
            self.notifier.send(subject, body)
        if send_major_email:
            self.send_major_event_email_batch([event])
        if event.severity == "high" and self.openai.configured and not self.dry_run and self.llm_analysis_available():
            self.runtime.enqueue_job(
                "major_event_analysis",
                {"event": self._event_payload(event), "memory_context": memory_context, "idempotency_key": key},
                priority=100,
                idempotency_key=f"ai:{key}",
            )

    def send_realtime_event_batch(self, events: list[MarketEvent]) -> None:
        if not events:
            return
        subject = f"Portfolio alerts: {len(events)} events"
        body = "\n".join(f"- {event.symbol} {event.event_type}: {event.message}" for event in events[:12])
        self.notifier.send(subject, body)

    def llm_analysis_available(self) -> bool:
        return self.runtime.check_and_touch_cooldown(LLM_ANALYSIS_BACKOFF_KEY, LLM_ANALYSIS_BACKOFF, commit=False)

    def openai_analysis_available(self) -> bool:
        return self.llm_analysis_available()

    def send_major_event_email_batch(self, events: list[MarketEvent]) -> None:
        if self.dry_run or not events:
            return
        email_events = [event for event in events if self.should_send_major_event_email(event)]
        if not email_events:
            return
        if len(email_events) == 1:
            event = email_events[0]
            subject = f"Portfolio major alert: {event.symbol} {event.event_type}"
            body = format_major_event_email(event)
        else:
            subject = f"Portfolio major alerts: {len(email_events)} events"
            body = format_major_event_email_batch(email_events)
        AgentMailNotifier(self.config).send(subject, body)

    def should_send_major_event_email(self, event: MarketEvent) -> bool:
        if self.dry_run:
            return False
        if not self.config.notifications.agentmail_enabled or not self.config.notifications.agentmail_major_alerts_enabled:
            return False
        if event.severity != "high":
            return False
        session = market_session_now(self.config.timezone)
        if session == "market_hours":
            if not major_event_email_worthy(event):
                return False
        elif not off_hours_major_event_email_worthy(
            event,
            extreme_move_percent=self.config.notifications.agentmail_off_hours_extreme_move_percent,
        ):
            return False
        minutes = (
            self.config.notifications.agentmail_market_hours_cooldown_minutes
            if session == "market_hours"
            else self.config.notifications.agentmail_off_hours_cooldown_minutes
        )
        cooldown = timedelta(minutes=max(1, minutes))
        key = f"agentmail_major_alert:{event.symbol}:{event.event_type}"
        return self.runtime.check_and_touch_cooldown(key, cooldown, commit=True)

    def _apply_cooldown(self, events: list[MarketEvent], commit: bool = True) -> list[MarketEvent]:
        keep: list[MarketEvent] = []
        cooldown = timedelta(minutes=self.config.monitor.major_move_cooldown_minutes)
        for event in events:
            key = f"{event.symbol}:{event.event_type}"
            if self.runtime.check_and_touch_cooldown(key, cooldown, commit=commit):
                keep.append(event)
        return keep

    @staticmethod
    def _event_payload(event: MarketEvent) -> dict:
        return {
            "symbol": event.symbol,
            "event_type": event.event_type,
            "severity": event.severity,
            "message": event.message,
            "news": {
                "title": event.news.title,
                "url": event.news.url,
                "source": event.news.source,
                "summary": event.news.summary,
                "symbols": event.news.symbols,
                "kind": event.news.kind,
            }
            if event.news
            else None,
        }


def percent_change(old: float, new: float) -> float:
    if old == 0:
        return 0.0
    return ((new - old) / old) * 100.0


def is_high_impact_lead(text: str) -> bool:
    return high_impact_score(text) >= 7


def high_impact_score(text: str) -> int:
    lowered = text.lower()
    keyword_scores = {
        "raises guidance": 10,
        "cuts guidance": 10,
        "guidance raise": 9,
        "guidance cut": 9,
        "sec investigation": 10,
        "bankruptcy": 10,
        "going concern": 10,
        "design win": 8,
        "customer qualification": 8,
        "major contract": 8,
        "strategic partnership": 7,
        "public offering": 7,
        "stock offering": 7,
        "private placement": 7,
        "earnings beat": 7,
        "earnings miss": 7,
        "beats estimates": 7,
        "misses estimates": 7,
        "revenue warning": 8,
        "margin expansion": 7,
        "chips act award": 8,
    }
    return max((score for keyword, score in keyword_scores.items() if keyword in lowered), default=0)


def select_high_impact_news(news, max_items: int = 5, symbols: set[str] | None = None):
    ranked = []
    seen_symbols: set[str] = set()
    seen_titles: set[str] = set()
    symbol_filter = {symbol.upper() for symbol in symbols or set()}
    for item in news:
        item_symbols = {symbol.upper() for symbol in item.symbols}
        matched_symbols = item_symbols & symbol_filter if symbol_filter else item_symbols
        if symbol_filter:
            if item_symbols and not matched_symbols:
                continue
            if matched_symbols and not any(news_title_matches_symbol(item.title, symbol) for symbol in matched_symbols):
                continue
        score = high_impact_score(item.title)
        if score < 7:
            continue
        title_key = item.title.strip().lower()
        if title_key in seen_titles:
            continue
        primary_symbol = item.symbols[0].upper() if item.symbols else ""
        if primary_symbol and primary_symbol in seen_symbols:
            continue
        seen_titles.add(title_key)
        if primary_symbol:
            seen_symbols.add(primary_symbol)
        ranked.append((score, item))
    ranked.sort(key=lambda row: (row[0], row[1].published_at or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
    return [item for _, item in ranked[:max_items]]


def holding_alert_symbols(holdings: list[Holding]) -> set[str]:
    symbols: set[str] = set()
    for holding in holdings:
        if holding.asset_type in {"equity", "etf"}:
            symbol = holding.normalized_symbol()
            symbols.add(symbol)
            exposure = leveraged_exposure(symbol)
            if exposure:
                symbols.add(exposure.underlying)
        elif holding.asset_type == "option":
            underlying = str(holding.metadata.get("underlying", "")).strip().upper()
            if underlying:
                symbols.add(underlying)
    return symbols


def news_title_matches_symbol(title: str, symbol: str) -> bool:
    return symbol_matches_text(symbol, title)


def market_session_now(timezone_name: str) -> str:
    now = datetime.now(ZoneInfo(timezone_name))
    if now.weekday() >= 5:
        return "off_hours"
    minutes = now.hour * 60 + now.minute
    if 9 * 60 + 30 <= minutes <= 16 * 60:
        return "market_hours"
    return "off_hours"


def major_event_email_worthy(event: MarketEvent) -> bool:
    if event.news:
        return high_impact_score(f"{event.news.title} {event.news.summary}") >= 7
    pct = abs(float((event.metadata or {}).get("change_percent", 0.0) or 0.0))
    return pct >= 5.0 or event.event_type in {"portfolio_move", "high_impact_news"}


def off_hours_major_event_email_worthy(event: MarketEvent, *, extreme_move_percent: float = 8.0) -> bool:
    if event.news:
        text = f"{event.news.title} {event.news.summary}"
        return high_impact_score(text) >= 7 or is_earnings_related(text)
    pct = abs(float((event.metadata or {}).get("change_percent", 0.0) or 0.0))
    return pct >= max(5.0, extreme_move_percent)


def is_earnings_related(text: str) -> bool:
    lowered = text.lower()
    keywords = [
        "earnings",
        "quarterly results",
        "q1 results",
        "q2 results",
        "q3 results",
        "q4 results",
        "eps",
        "revenue",
        "guidance",
    ]
    return any(keyword in lowered for keyword in keywords)


def format_major_event_email(event: MarketEvent) -> str:
    lines = [
        f"标的: {event.symbol}",
        f"事件: {event.event_type}",
        f"级别: {event.severity}",
        f"结论: {event.message}",
    ]
    if event.metadata and "change_percent" in event.metadata:
        lines.append(f"变化幅度: {float(event.metadata['change_percent']):+.2f}%")
    if event.news:
        lines.extend(
            [
                "",
                "相关新闻:",
                f"- 来源: {event.news.source}",
                f"- 标题: {event.news.title}",
                f"- 摘要: {event.news.summary or '暂无摘要'}",
                f"- 参考: {event.news.url or '无链接'}",
            ]
        )
    lines.extend(
        [
            "",
            "处理原则:",
            "- 这是研究和风险提醒，不是自动买卖指令。",
            "- iMessage 会继续发短快讯；邮箱只发送达到阈值的大行情/重大新闻和日报。",
        ]
    )
    return "\n".join(lines)


def format_major_event_email_batch(events: list[MarketEvent]) -> str:
    if len(events) == 1:
        return format_major_event_email(events[0])
    lines = [
        f"合并重大提醒: {len(events)} 个事件",
        "本邮件把同一轮扫描里符合邮箱阈值的大行情/重大新闻合并发送，避免多封邮件刷屏。",
        "",
    ]
    for index, event in enumerate(events, start=1):
        lines.extend(
            [
                f"## {index}. {event.symbol} {event.event_type}",
                format_major_event_email(event),
                "",
            ]
        )
    return "\n".join(lines).strip()


def network_available() -> bool:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=3):
            return True
    except OSError:
        return False

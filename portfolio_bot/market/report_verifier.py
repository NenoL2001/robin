from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ..config import BotConfig
from ..memory import MemoryStore, memory_path
from ..models import Holding
from .relations import StoredSymbolRelation


UNCERTAIN_NEWS_PATTERNS = ("未发现新闻", "未发现公开新闻", "待查证", "暂无直接新闻", "缺乏信息源")


@dataclass(slots=True)
class ReportVerificationResult:
    ok: bool
    blocked: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        status = "blocked" if self.blocked else "ok"
        parts = [f"report_verification={status}"]
        if self.errors:
            parts.append("errors=" + "; ".join(self.errors[:5]))
        if self.warnings:
            parts.append("warnings=" + "; ".join(self.warnings[:5]))
        return " | ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["summary"] = self.summary
        return payload


class ReportVerifier:
    def __init__(self, config: BotConfig, *, memory: MemoryStore | None = None):
        self.config = config
        self.memory = memory or MemoryStore(memory_path(config.data_dir, config.memory.sqlite_path), enabled=config.memory.enabled)

    def verify(
        self,
        report: str,
        holdings: list[Holding],
        relationships: list[StoredSymbolRelation],
        *,
        query_log: list[str] | None = None,
        now: datetime | None = None,
        commit: bool = False,
    ) -> ReportVerificationResult:
        if not self.config.report_verifier.enabled:
            return ReportVerificationResult(ok=True, blocked=False)
        now = now or datetime.now(configured_timezone(self.config.timezone))
        errors: list[str] = []
        warnings: list[str] = []
        query_log = query_log or []
        check_dates(report, now, errors, warnings, block_on_wrong_date=self.config.report_verifier.block_on_wrong_date)
        check_quantity_market_value(report, holdings, errors, warnings)
        check_relationship_disclosure(report, holdings, relationships, errors)
        check_uncertain_without_query(report, query_log, errors, warnings)
        repeated = repeated_paragraphs(report)
        if repeated:
            errors.append(f"重复段落过多: {len(repeated)}")
        result = ReportVerificationResult(
            ok=not errors,
            blocked=bool(errors),
            errors=errors,
            warnings=warnings,
            metadata={
                "query_count": len(query_log),
                "relationship_count": len(relationships),
                "repeated_paragraphs": repeated[:5],
                "run_date": now.date().isoformat(),
            },
        )
        if commit:
            self.memory.add(
                "report_verification",
                result.summary,
                importance=0.86 if result.blocked else 0.62,
                confidence=0.9,
                source="report_verifier",
                metadata=result.to_dict(),
            )
        return result


def check_dates(report: str, now: datetime, errors: list[str], warnings: list[str], *, block_on_wrong_date: bool) -> None:
    today = now.date().isoformat()
    lines = [line.strip() for line in (report or "").splitlines() if line.strip()]
    explicit_dates: set[str] = set()
    for line in lines[:5]:
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", line) or re.match(r"^20\d{2}-\d{2}-\d{2}\s+(日报|报告|Portfolio|半导体)", line):
            explicit_dates.update(re.findall(r"\b20\d{2}-\d{2}-\d{2}\b", line))
    for match in re.finditer(r"(报告日期|运行日|生成日期|日期|Date)[:：\s]*(20\d{2}-\d{2}-\d{2})", report or "", flags=re.IGNORECASE):
        explicit_dates.add(match.group(2))
    for year, month, day in re.findall(r"(?:报告日期|运行日|生成日期|日期)[:：\s]*(20\d{2})年(\d{1,2})月(\d{1,2})日", report or ""):
        explicit_dates.add(f"{int(year):04d}-{int(month):02d}-{int(day):02d}")
    wrong = sorted(date for date in explicit_dates if date != today)
    if wrong and block_on_wrong_date:
        errors.append(f"报告日期不等于运行日 {today}: {', '.join(wrong[:4])}")
    elif wrong:
        warnings.append(f"报告日期疑似非运行日 {today}: {', '.join(wrong[:4])}")


def check_quantity_market_value(report: str, holdings: list[Holding], errors: list[str], warnings: list[str]) -> None:
    text = report or ""
    for holding in holdings:
        symbol = holding.normalized_symbol()
        if not symbol or holding.market_value is None or holding.quantity is None:
            continue
        if abs(float(holding.quantity or 0.0) - float(holding.market_value or 0.0)) < 0.01:
            continue
        local = symbol_context(text, symbol)
        if not local or "市值" not in local:
            continue
        quantity_patterns = [
            rf"市值[^\n$￥0-9]{{0,8}}\$?\s*{re.escape(format_number(holding.quantity))}\b",
            rf"市值[^\n$￥0-9]{{0,8}}\$?\s*{holding.quantity:.2f}\b",
        ]
        if any(re.search(pattern, local) for pattern in quantity_patterns):
            errors.append(f"{symbol} 疑似把 quantity 当 market value")
        elif re.search(r"市值[^\n]{0,30}\$?\s*\d+(\.\d+)?", local) and f"{holding.market_value:.2f}" not in local and format_number(float(holding.market_value)) not in local:
            warnings.append(f"{symbol} 市值表述未匹配 holdings.market_value")


def check_relationship_disclosure(report: str, holdings: list[Holding], relationships: list[StoredSymbolRelation], errors: list[str]) -> None:
    text_upper = (report or "").upper()
    held = {holding.normalized_symbol() for holding in holdings if holding.asset_type in {"equity", "etf"}}
    relation_map = {relation.source_symbol: relation for relation in relationships if relation.confidence >= 0.55}
    for symbol in sorted(held & set(relation_map)):
        relation = relation_map[symbol]
        if symbol not in text_upper:
            continue
        relation_terms = [relation.related_symbol.upper(), "底层", "经济暴露", "UNDERLYING", "2X"]
        if not any(term in text_upper for term in relation_terms):
            errors.append(f"{symbol} 缺少底层关系披露 {relation.related_symbol}")


def check_uncertain_without_query(report: str, query_log: list[str], errors: list[str], warnings: list[str]) -> None:
    if not any(pattern in report for pattern in UNCERTAIN_NEWS_PATTERNS):
        return
    has_query_text = bool(re.search(r"(已执行\s*query|strategy_scout|checked=|query:|site:|搜索)", report, flags=re.IGNORECASE))
    if not query_log and not has_query_text:
        errors.append("报告写了未发现/待查证，但没有展示已执行 query log")
    elif len(query_log) < 2 and not has_query_text:
        warnings.append("待查证表述的 query log 过少")


def repeated_paragraphs(report: str) -> list[str]:
    counts: dict[str, int] = {}
    for paragraph in re.split(r"\n\s*\n", report or ""):
        normalized = " ".join(paragraph.split()).strip().lower()
        if len(normalized) < 90:
            continue
        counts[normalized] = counts.get(normalized, 0) + 1
    return [text[:120] for text, count in counts.items() if count > 1]


def symbol_context(text: str, symbol: str, *, window: int = 220) -> str:
    match = re.search(rf"(?<![A-Z0-9]){re.escape(symbol.upper())}(?![A-Z0-9])", text.upper())
    if not match:
        return ""
    start = max(0, match.start() - window)
    end = min(len(text), match.end() + window)
    return text[start:end]


def format_number(value: float) -> str:
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"


def fallback_verified_report(report: str, verification: ReportVerificationResult) -> str:
    head = "\n".join((report or "").splitlines()[:80])
    return (
        "半导体与持仓机会日报\n\n"
        "## 本地校验降级\n"
        "原报告未通过发送前校验，已阻断重复或不可信内容发送。"
        f"\n{verification.summary}\n\n"
        "## 原报告节选\n"
        f"{head}"
    )


def configured_timezone(name: str):
    try:
        return ZoneInfo(name or "UTC")
    except Exception:
        return timezone.utc

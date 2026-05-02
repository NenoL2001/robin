from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from ..models import AnalyzedNewsItem, Holding, NewsEvidence, NewsItem
from .exposures import leveraged_exposure
from .metrics import generic_market_article, keyword_matches_text, news_relevance, symbol_matches_text


POSITIVE_TERMS = {
    "raises guidance": "上调指引",
    "guidance above": "指引好于预期",
    "design win": "设计导入/客户采用",
    "customer qualification": "客户认证",
    "major contract": "重大合同",
    "production order": "生产订单",
    "partnership": "合作",
    "earnings beat": "业绩超预期",
    "beats estimates": "超过预期",
    "margin expansion": "利润率改善",
}

RISK_TERMS = {
    "public offering": "融资/增发",
    "stock offering": "股票发行",
    "private placement": "私募融资",
    "dilution": "摊薄风险",
    "cuts guidance": "下调指引",
    "misses estimates": "不及预期",
    "earnings miss": "业绩不及预期",
    "going concern": "持续经营风险",
    "sec investigation": "监管调查",
    "bankruptcy": "破产风险",
}

STRATEGY_TERMS = {
    "semiconductor": "半导体链路",
    "silicon photonics": "硅光/光互联",
    "advanced packaging": "先进封装",
    "hbm": "AI 存储链路",
    "ai": "AI 需求",
    "wafer": "晶圆/材料",
    "test": "测试设备",
    "burn-in": "老化测试",
    "order": "订单",
    "revenue growth": "收入增长",
}


def analyze_news_items(
    news: list[NewsItem],
    holdings: list[Holding],
    research_symbols: set[str] | None = None,
    limit: int = 15,
    min_relevance: float = 0.0,
) -> list[AnalyzedNewsItem]:
    context = exposure_context(holdings)
    research_symbols = {symbol.upper() for symbol in (research_symbols or set())}
    seen: set[str] = set()
    analyzed: list[AnalyzedNewsItem] = []
    for item in news:
        key = item.dedupe_key()
        if key in seen:
            continue
        seen.add(key)
        analyzed_item = analyze_news_item(item, context, research_symbols)
        text = f"{item.title} {item.summary}".lower()
        if analyzed_item.relevance_score < min_relevance and (analyzed_item.relation == "research" or generic_market_article(text)):
            continue
        analyzed.append(analyzed_item)
    return sorted(analyzed, key=news_priority)[:limit]


def analyze_news_item(item: NewsItem, context: dict[str, dict[str, str] | set[str]], research_symbols: set[str]) -> AnalyzedNewsItem:
    item_symbols = {symbol.upper() for symbol in item.symbols if symbol}
    title_text = item.title
    text = f"{item.title} {item.summary}"
    if not item_symbols:
        for symbol in research_symbols:
            if symbol_matches_text(symbol, text):
                item_symbols.add(symbol)
        for symbol in context["all_exposure_symbols"]:
            if symbol_matches_text(symbol, text):
                item_symbols.add(symbol)
    relation, impacted = relation_and_impacted(item_symbols, context, title_text)
    lower_text = text.lower()
    positives = [label for key, label in POSITIVE_TERMS.items() if keyword_matches_text(key, lower_text)]
    risks = [label for key, label in RISK_TERMS.items() if keyword_matches_text(key, lower_text)]
    strategy_hits = [label for key, label in STRATEGY_TERMS.items() if keyword_matches_text(key, lower_text)]
    relevance = relevance_for(item, item_symbols, relation, research_symbols)
    confidence = confidence_for(item, relation, positives, risks, relevance)
    symbols = sorted(item_symbols) or sorted(research_symbols & item_symbols)
    if not symbols and impacted:
        symbols = sorted({symbol_from_holding_name(value) for value in impacted if value})
    summary = chinese_summary(item, positives, risks, strategy_hits)
    evidence = news_evidence(item, symbols, relation, research_symbols, positives, risks, strategy_hits, relevance, confidence)
    return AnalyzedNewsItem(
        symbols=symbols,
        source=item.source,
        title=item.title,
        url=item.url,
        published_at=item.published_at,
        chinese_summary=summary,
        portfolio_impact=portfolio_impact_text(relation, impacted),
        strategy_relevance=strategy_relevance_text(strategy_hits, relation),
        confidence=confidence,
        risk_flags=risk_flags(item, risks, relation),
        why_it_matters=why_it_matters_text(relation, positives, risks, strategy_hits),
        relation=relation,
        relevance_score=relevance,
        category=evidence.category,
        source_label=source_label(evidence),
        key_points=evidence.key_points,
    )


def exposure_context(holdings: list[Holding]) -> dict[str, dict[str, str] | set[str]]:
    direct: dict[str, str] = {}
    option_underlyings: dict[str, str] = {}
    for holding in holdings:
        if holding.asset_type == "option":
            underlying = str(holding.metadata.get("underlying", "")).strip().upper()
            if underlying:
                option_underlyings[underlying] = holding.name or holding.symbol
        else:
            symbol = holding.normalized_symbol()
            direct[symbol] = holding.name or holding.symbol
            exposure = leveraged_exposure(symbol)
            if exposure:
                direct[exposure.underlying] = f"{holding.name or holding.symbol} ({exposure.chinese_label})"
    return {
        "direct": direct,
        "option_underlyings": option_underlyings,
        "all_exposure_symbols": set(direct) | set(option_underlyings),
    }


def relation_and_impacted(item_symbols: set[str], context: dict[str, dict[str, str] | set[str]], title_text: str) -> tuple[str, list[str]]:
    direct = context["direct"]
    option_underlyings = context["option_underlyings"]
    impacted: list[str] = []
    for symbol, name in direct.items():
        if symbol in item_symbols or symbol_matches_text(symbol, title_text):
            impacted.append(f"{symbol} ({name})")
    for symbol, name in option_underlyings.items():
        if symbol in item_symbols or symbol_matches_text(symbol, title_text):
            impacted.append(f"{symbol} 期权 ({name})")
    if impacted:
        if any("期权" not in item for item in impacted):
            return "real_holding", impacted
        return "option_underlying", impacted
    return "research", impacted


def confidence_for(item: NewsItem, relation: str, positives: list[str], risks: list[str], relevance: float = 0.0) -> float:
    if item.kind == "x_post" or item.source.lower() in {"x", "twitter"}:
        base = 0.35
    elif item.url:
        base = 0.58
    else:
        base = 0.45
    if relation in {"real_holding", "option_underlying"}:
        base += 0.08
    if positives or risks:
        base += 0.07
    if relevance >= 0.75:
        base += 0.05
    elif relevance < 0.45:
        base -= 0.08
    return round(max(0.2, min(0.85, base)), 2)


def chinese_summary(item: NewsItem, positives: list[str], risks: list[str], strategy_hits: list[str]) -> str:
    signals = positives + risks + strategy_hits
    if signals:
        return f"{item.source} 的线索显示：{item.title}。关键词：{', '.join(signals[:5])}。"
    return f"{item.source} 的线索显示：{item.title}。当前更像信息流/市场情绪线索，需要进一步验证。"


def portfolio_impact_text(relation: str, impacted: list[str]) -> str:
    if impacted:
        return f"影响到真实持仓/期权底层：{', '.join(impacted[:6])}。需要结合价格、成交量、公告或财报验证。"
    if relation == "research":
        return "未直接命中真实持仓，主要作为研究池或行业线索。"
    return "与真实持仓关系不明确。"


def strategy_relevance_text(strategy_hits: list[str], relation: str) -> str:
    if strategy_hits:
        return f"与策略相关：{', '.join(strategy_hits[:5])}。可用于判断订单、业绩拐点、链路景气或估值反转。"
    if relation in {"real_holding", "option_underlying"}:
        return "与持仓相关，但暂未出现明确订单/业绩/估值反转关键词。"
    return "与当前半导体反转策略相关性一般，保留观察。"


def risk_flags(item: NewsItem, risks: list[str], relation: str) -> list[str]:
    flags = list(risks)
    if item.kind == "x_post" or item.source.lower() in {"x", "twitter"}:
        flags.append("X/社媒线索，不能单独当事实")
    if relation == "research":
        flags.append("未直接影响真实持仓")
    return flags[:6]


def why_it_matters_text(relation: str, positives: list[str], risks: list[str], strategy_hits: list[str]) -> str:
    if risks:
        return f"需要关注风险是否会改变仓位的风险收益：{', '.join(risks[:4])}。"
    if positives:
        return f"可能影响收入预期、订单节奏或估值修复：{', '.join(positives[:4])}。"
    if strategy_hits:
        return f"可能影响半导体链路判断：{', '.join(strategy_hits[:4])}。"
    if relation in {"real_holding", "option_underlying"}:
        return "虽然没有强催化词，但它直接关联真实持仓，适合继续跟踪。"
    return "目前主要用于扩展行业背景，不应单独触发高置信结论。"


def news_priority(item: AnalyzedNewsItem) -> tuple[int, float, float]:
    relation_rank = {"real_holding": 0, "option_underlying": 1, "research": 2}.get(item.relation, 3)
    category_rank = {"portfolio": 0, "semiconductor": 1, "crypto": 2, "macro": 3, "other": 4}.get(item.category, 5)
    return (relation_rank, category_rank, -(item.relevance_score + item.confidence))


def format_analyzed_news_section(items: list[AnalyzedNewsItem]) -> str:
    if not items:
        return "暂无新新闻或 X 线索；如果未配置新闻/API 数据源，这里会为空。"
    groups = [
        ("真实持仓/期权底层", [item for item in items if item.relation in {"real_holding", "option_underlying"}]),
        ("半导体研究池", [item for item in items if item.relation == "research" and item.category == "semiconductor"]),
        ("其他需观察", [item for item in items if item.relation == "research" and item.category != "semiconductor"]),
    ]
    blocks: list[str] = []
    for group_name, group_items in groups:
        if not group_items:
            continue
        blocks.append(f"### {group_name}")
        for item in group_items[:15]:
            if is_finnhub_api_url(item.url):
                reference = f"来源: {item.source_label}; Finnhub 链接: {item.url}"
            else:
                reference = f"来源: [{item.source_label or item.source}]({item.url})" if item.url else f"来源: {item.source_label or item.source}"
            key_points = "；".join(item.key_points[:3]) if item.key_points else "暂无可提炼摘要，需打开来源核验。"
            published = item.published_at.date().isoformat() if item.published_at else "未知时间"
            symbols = ", ".join(item.symbols[:6]) if item.symbols else "未明确"
            risks = "；".join(item.risk_flags) if item.risk_flags else "暂无明显风险标记"
            blocks.append(
                "\n".join(
                    [
                        f"- 标题: {item.title}",
                        f"  时间/标的: {published}; {symbols}",
                        f"  发生了什么: {key_points}",
                        f"  影响: {item.portfolio_impact}",
                        f"  策略意义: {item.strategy_relevance}",
                        f"  置信度: {item.confidence:.2f}; 相关性: {item.relevance_score:.2f}; 风险: {risks}",
                        f"  {reference}",
                    ]
                )
            )
    return "\n".join(blocks)


def relevance_for(item: NewsItem, item_symbols: set[str], relation: str, research_symbols: set[str]) -> float:
    candidates = item_symbols or {symbol for symbol in research_symbols if symbol_matches_text(symbol, f"{item.title} {item.summary}")}
    if not candidates:
        return 0.0
    score = max(news_relevance(item, symbol) for symbol in candidates)
    if relation in {"real_holding", "option_underlying"}:
        score += 0.1
    return round(max(0.0, min(1.0, score)), 3)


def news_evidence(
    item: NewsItem,
    symbols: list[str],
    relation: str,
    research_symbols: set[str],
    positives: list[str],
    risks: list[str],
    strategy_hits: list[str],
    relevance: float,
    confidence: float,
) -> NewsEvidence:
    via = "Finnhub" if is_finnhub_api_url(item.url) or item.kind == "company_news" else item.source
    canonical = "" if is_finnhub_api_url(item.url) else item.url
    flags = risk_flags(item, risks, relation)
    category = news_category(symbols, relation, research_symbols, strategy_hits, item)
    return NewsEvidence(
        publisher=item.source or via,
        via_source=via,
        source_url=item.url,
        canonical_url=canonical,
        published_at=item.published_at,
        symbols=symbols,
        summary=item.summary or item.title,
        key_points=key_points_for(item, positives, risks, strategy_hits),
        relevance_score=relevance,
        confidence=confidence,
        risk_flags=flags,
        title=item.title,
        relation=relation,
        category=category,
    )


def key_points_for(item: NewsItem, positives: list[str], risks: list[str], strategy_hits: list[str]) -> list[str]:
    points: list[str] = []
    summary = " ".join((item.summary or "").split())
    if summary:
        for sentence in split_sentences(summary):
            if sentence and sentence not in points:
                points.append(sentence)
            if len(points) >= 2:
                break
    if positives:
        points.append("正面催化: " + ", ".join(positives[:3]))
    if risks:
        points.append("风险信号: " + ", ".join(risks[:3]))
    if strategy_hits:
        points.append("策略相关: " + ", ".join(strategy_hits[:3]))
    if not points:
        points.append(item.title)
    return points[:4]


def split_sentences(text: str) -> list[str]:
    parts: list[str] = []
    current = ""
    for index, char in enumerate(text):
        current += char
        if char == "." and index > 0 and index + 1 < len(text) and text[index - 1].isdigit() and text[index + 1].isdigit():
            continue
        if char in ".!?。！？":
            parts.append(current.strip())
            current = ""
    if current.strip():
        parts.append(current.strip())
    return [part[:240] for part in parts if part.strip()]


def news_category(symbols: list[str], relation: str, research_symbols: set[str], strategy_hits: list[str], item: NewsItem) -> str:
    symbol_set = {symbol.upper() for symbol in symbols}
    if symbol_set & {"BTC", "ETH", "DOGE", "XRP", "SOL"}:
        return "crypto"
    if relation in {"real_holding", "option_underlying"}:
        return "portfolio"
    text = f"{item.title} {item.summary}".lower()
    semiconductor_terms = {"半导体链路", "硅光/光互联", "先进封装", "AI 存储链路", "测试设备"} & set(strategy_hits)
    if symbol_set & research_symbols or semiconductor_terms or any(term in text for term in ("semiconductor", "chip", "wafer", "foundry", "hbm", "ai server")):
        return "semiconductor"
    if any(term in text for term in ("oil", "fed", "inflation", "market", "s&p", "iran")):
        return "macro"
    return "other"


def source_label(evidence: NewsEvidence) -> str:
    if evidence.via_source and evidence.via_source != evidence.publisher:
        return f"{evidence.publisher} via {evidence.via_source}"
    return evidence.publisher or evidence.via_source


def is_finnhub_api_url(url: str) -> bool:
    return "finnhub.io/api/news" in (url or "").lower()


def analyzed_news_to_dict(item: AnalyzedNewsItem) -> dict:
    payload = asdict(item)
    published = payload.get("published_at")
    if isinstance(published, datetime):
        payload["published_at"] = published.isoformat()
    return payload


def symbol_from_holding_name(value: str) -> str:
    return value.split(" ", 1)[0].replace("(", "").strip().upper()

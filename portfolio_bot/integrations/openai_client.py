from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

from ..config import BotConfig
from ..market.exposures import leveraged_exposure
from ..models import Holding, MarketEvent, NewsItem, StrategyScore


class OpenAIService:
    """Compatibility wrapper around the configured OpenAI-compatible LLM provider."""

    def __init__(self, config: BotConfig):
        self.config = config
        self._client = None

    @property
    def provider(self) -> str:
        return self.config.llm.provider

    @property
    def api_key(self) -> str:
        return self.config.llm.api_key or self.config.openai_api_key

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    @property
    def client(self):
        if not self.api_key:
            raise RuntimeError(f"{self.provider} LLM API key is not configured")
        if self._client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {"api_key": self.api_key}
            if self.config.llm.base_url:
                kwargs["base_url"] = self.config.llm.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def import_holdings_from_screenshot(self, image_path: Path) -> list[Holding]:
        model = self.config.llm.vision_model
        if not model:
            raise RuntimeError(
                "Screenshot import requires a vision-capable LLM model. "
                "Set LLM_VISION_MODEL and provider/base_url for a vision provider."
            )
        data = base64.b64encode(image_path.read_bytes()).decode("ascii")
        suffix = image_path.suffix.lower().lstrip(".") or "png"
        prompt = (
            "Extract portfolio holdings from this user-provided brokerage screenshot. "
            "Return only visible positions as a JSON object with a holdings array. "
            "Each holding must include symbol, name, asset_type, quantity, market_value, avg_cost, and confidence. "
            "Use null for unclear numeric fields and low confidence for uncertain rows."
        )
        text = self._chat_text(
            model,
            [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/{suffix};base64,{data}"}},
                    ],
                }
            ],
            response_format={"type": "json_object"},
        )
        payload = self._parse_json_text(text)
        return [
            Holding(
                symbol=str(item["symbol"]).upper(),
                name=str(item.get("name", "")),
                asset_type=item.get("asset_type", "unknown"),
                quantity=float(item.get("quantity", 0) or 0),
                market_value=item.get("market_value"),
                avg_cost=item.get("avg_cost"),
                metadata={"ocr_confidence": item.get("confidence"), "llm_provider": self.provider},
            )
            for item in payload.get("holdings", [])
        ]

    def analyze_event(self, event: MarketEvent, related_news: list[NewsItem], memory_context: str = "") -> str:
        news_lines = "\n".join(f"- {item.source}: {item.title} ({item.url})" for item in related_news[:8])
        prompt = f"""
You are a disciplined market research assistant. Analyze this portfolio event in Chinese.
Do not give direct buy/sell orders. Focus on facts, likely drivers, risks, and what to verify.

Event:
{event.message}

Related news:
{news_lines or "No confirmed related news found."}

Relevant memory:
{memory_context or "No relevant historical memory found."}
"""
        return self._chat_text(
            self.config.llm.event_model or self.config.openai_event_model,
            [{"role": "user", "content": prompt}],
        )

    def generate_report(
        self,
        title: str,
        holdings: list[Holding],
        scores: list[StrategyScore],
        news: list[NewsItem],
        memory_context: str = "",
        paper_summary: str = "",
        skill_summary: str = "",
        backtest_summary: str = "",
        agent_summary: str = "",
    ) -> str:
        holdings_text = "\n".join(format_holding_for_prompt(holding) for holding in holdings)
        scores_text = "\n".join(
            f"- {s.symbol}: score={s.score:.1f}, confidence={s.confidence:.2f}, catalysts={', '.join(s.catalysts[:4])}"
            for s in scores[:20]
        )
        numeric_text = "\n".join(format_numeric_score_for_prompt(score) for score in scores[:12])
        news_text = "\n".join(f"- {n.source}: {n.title} ({n.url})" for n in news[:30])
        prompt = f"""
请用中文写一份研究报告，标题为「{title}」。
这是研究候选清单和模拟交易复盘，不是真实买卖指令。
本地确定性数值系统已经完成行情行为、新闻证据、因子贡献、风险门和持仓暴露计算；你的任务是综合和解释这些数值，不要绕过它重新凭新闻标题下结论。

真实持仓:
{holdings_text}

策略评分:
{scores_text}

本地数值因子摘要:
{numeric_text}

相关新闻:
{news_text}

模拟组合:
{paper_summary or "暂无模拟组合数据。"}

策略 Skill 状态:
{skill_summary or "暂无策略 Skill 状态。"}

最近回测:
{backtest_summary or "暂无回测记录。"}

Agent 运行状态:
{agent_summary or "暂无 Agent 运行记录。"}

相关持久记忆:
{memory_context or "No relevant historical memory found."}

必须包含这些小节：
1. 今日组合概览
2. 大行情和重大新闻
3. 当前真实持仓观察
4. 模拟组合净值和盈亏
5. 策略 Skill 表现
6. 长期期权候选
7. 半导体链路机会
8. 今日复盘和明日关注
9. 新策略候选/是否需要创建新 Skill
10. Agent 运行状态
"""
        return self._chat_text(
            self.config.llm.event_model or self.config.openai_event_model,
            [{"role": "user", "content": prompt}],
        )

    def _chat_text(self, model: str, messages: list[dict[str, Any]], *, response_format: dict[str, Any] | None = None) -> str:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if response_format:
            kwargs["response_format"] = response_format
        extra_body = self._extra_body_for_model(model)
        if extra_body:
            kwargs["extra_body"] = extra_body
        response = self.client.chat.completions.create(**kwargs)
        return self._response_text(response)

    def _extra_body_for_model(self, model: str) -> dict[str, Any]:
        if self.provider != "deepseek":
            return {}
        thinking_enabled = self.config.llm.event_thinking_enabled
        if model == self.config.llm.monitor_model or model == self.config.llm.vision_model:
            thinking_enabled = self.config.llm.monitor_thinking_enabled
        payload: dict[str, Any] = {"thinking": {"type": "enabled" if thinking_enabled else "disabled"}}
        if thinking_enabled and self.config.llm.reasoning_effort:
            payload["reasoning_effort"] = self.config.llm.reasoning_effort
        return payload

    @staticmethod
    def _response_text(response: Any) -> str:
        if isinstance(response, dict):
            choices = response.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                return str(message.get("content") or "")
            return str(response.get("output_text", response))
        choices = getattr(response, "choices", None)
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None) if message else None
            if content is not None:
                return str(content)
        text = getattr(response, "output_text", None)
        if text:
            return str(text)
        return str(response)

    @staticmethod
    def _parse_json_text(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`")
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
        return json.loads(stripped)


def format_holding_for_prompt(holding: Holding) -> str:
    line = f"- {holding.symbol} {holding.quantity:g} {holding.asset_type}"
    exposure = leveraged_exposure(holding.normalized_symbol())
    if exposure:
        line += f"; leveraged exposure: {exposure.english_label}"
    return line


def format_numeric_score_for_prompt(score: StrategyScore) -> str:
    features = dict(score.metadata.get("features") or {})
    behavior = dict(features.get("daily_behavior") or {})
    factors = score.metadata.get("factor_breakdown") or []
    factor_text = "; ".join(
        f"{row.get('name')} value={row.get('value')} contribution={row.get('contribution')}"
        for row in factors[:8]
        if isinstance(row, dict)
    )
    flags = ", ".join(behavior.get("flags") or [])
    return (
        f"- {score.symbol}: local_score={score.score:.1f}, confidence={score.confidence:.2f}, "
        f"intraday={features.get('intraday_return_pct', 0):+.2f}%, behavior_score={features.get('daily_behavior_score', 0):+.2f}, "
        f"behavior_flags={flags or 'none'}, factors=[{factor_text or 'none'}]"
    )

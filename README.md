# Portfolio Bot

Realtime portfolio monitor and semiconductor opportunity research bot for macOS.

## What it does

- Runs on this Mac with `launchd`.
- Watches portfolio prices continuously, with a 5-minute deep news scan.
- Sends urgent alerts through iMessage and long reports through one merged email when AgentMail/email is configured.
- Uses the configured LLM provider; the default profile uses `deepseek-v4-flash` for routine monitoring and `deepseek-v4-pro` for major events and deep research.
- Imports Robinhood holdings from user-provided screenshots, then stores confirmed holdings locally.
- Maintains strategy skills under `strategy_skills/` so research rules and review memory accumulate over time.
- Persists local memory in SQLite/FTS and can optionally bridge to open-source memory backends such as `mem0`.
- Keeps a local paper portfolio and backtest ledger. Simulated orders are tied to strategy skills and signals.

This is a research and alerting tool. It does not place real broker trades.

## Quick start

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
cp config.example.yaml config.yaml
cp holdings.example.yaml holdings.yaml
cp analysts.example.yaml analysts.yaml
portfolio-bot validate-config --config config.yaml
portfolio-bot scan-once --config config.yaml --dry-run
```

Rotate any exposed LLM/API key before using this bot. Put new keys in `.env`, never in source code.

## Useful commands

```bash
portfolio-bot import-screenshot path/to/robinhood.png --config config.yaml
portfolio-bot test-imessage --config config.yaml --message "portfolio bot test"
portfolio-bot report-now --config config.yaml
portfolio-bot research-now POET HIMS --config config.yaml --send-email
portfolio-bot memory-search --config config.yaml "POET silicon photonics"
portfolio-bot memory-status --config config.yaml
portfolio-bot paper-buy --config config.yaml AEHR --quantity 1 --price 10 --strategy-name semiconductor_reversal --signal-id manual-1 --reason "manual paper test"
portfolio-bot paper-positions --config config.yaml
portfolio-bot paper-review --config config.yaml
portfolio-bot backtest --config config.yaml --prices "20,20.5,21,20.8,22,23.5,22.8,24"
portfolio-bot backtest --config config.yaml --asset-type option --prices "8,15,20" --strike 13 --premium 3.5
portfolio-bot backtest-report --config config.yaml
portfolio-bot runtime-status --config config.yaml
portfolio-bot workers-status --config config.yaml
portfolio-bot jobs --config config.yaml --status pending
portfolio-bot health-check --config config.yaml --dry-run
portfolio-bot profile-suite --config config.yaml --iterations 3
portfolio-bot profile-report --config config.yaml
portfolio-bot agent-run --config config.yaml operator_agent --objective "check bot health" --dry-run
portfolio-bot agent-status --config config.yaml --limit 10
portfolio-bot agent-trace --config config.yaml RUN_ID
portfolio-bot agent-doctor --config config.yaml
portfolio-bot run --config config.yaml
portfolio-bot install-launchd --config /Users/noah/robin/config.yaml
```

## Data sources

The bot is adapter-based. Configure only the sources you have keys for:

- Finnhub: quotes and company news.
- Tradier: option chains with Greeks/IV.
- X API: analyst and semiconductor keyword scanning.
- LLM provider: DeepSeek/OpenAI-compatible summaries, event analysis, deep research; screenshot import requires a configured vision-capable model.

When a key is missing, the related adapter returns no live data unless a command explicitly requires it.

## Code layout

The implementation is grouped by responsibility:

- `portfolio_bot/core/`: config, runtime SQLite, memory, storage, shared models.
- `portfolio_bot/market/`: realtime monitor, research reports, DataHub, feature calculation.
- `portfolio_bot/data/`: external market/news data adapters such as Finnhub, Tradier, X.
- `portfolio_bot/strategies/`: strategy skill interfaces and concrete strategy implementations.
- `portfolio_bot/trading/`: local-only paper portfolio and backtests.
- `portfolio_bot/agents/`: supervisor, worker loops, orchestrator, health/profile/maintenance agents, and the local agent harness.
- `portfolio_bot/agents/harness/`: deterministic planner, tool registry, guardrails, memory synthesis, trace persistence.
- `portfolio_bot/integrations/`: iMessage/email notifications, LLM provider client, launchd, screenshot import.

Root modules such as `portfolio_bot.monitor` remain compatibility wrappers so existing commands, tests, and running services keep the same import paths.

## Runtime workers

`portfolio-bot run` starts a local supervisor. The supervisor launches separate worker processes for realtime prices, news scanning, AI analysis, daily reports, local agents, paper jobs, and backtests.
Worker coordination lives in `.portfolio_bot/runtime.sqlite` with SQLite WAL enabled.

Useful commands:

```bash
portfolio-bot worker --config config.yaml realtime --once --dry-run
portfolio-bot worker --config config.yaml news --once --dry-run
portfolio-bot worker --config config.yaml ai --once --dry-run
portfolio-bot runtime-status --config config.yaml
portfolio-bot workers-status --config config.yaml
portfolio-bot jobs --config config.yaml --status failed
```

Dry-run worker commands do not write cooldowns, seen-news, or market jobs.

## Agent harness

The harness runs local operator/research/strategy/risk/report/maintenance/verification agents through a fixed loop: observe, synthesize memory, plan, guardrail, execute tools, verify, reflect, and persist. Agent plans, tasks, tool calls, trace spans, memory links, artifacts, and reflections are stored in `runtime.sqlite`.

Useful commands:

```bash
portfolio-bot agent-run --config config.yaml strategy_agent --objective "review strategy data gaps" --dry-run
portfolio-bot agent-memory-synthesize --config config.yaml "POET long call risk" --symbol POET
portfolio-bot agent-review --config config.yaml RUN_ID
portfolio-bot worker --config config.yaml agent --once --dry-run
```

Guardrails block `.env`, real broker trading, notification-recipient edits, and generic code patches outside the maintenance workflow. The daily orchestrator queues agent jobs when `agent_harness.enabled` and `workers.agent_processes` are enabled.

## Observability and profiling

Runtime telemetry is local-first:

- JSONL process logs: `.portfolio_bot/logs/portfolio-bot.jsonl`
- SQLite runtime logs, worker health checks, and profile samples: `.portfolio_bot/runtime.sqlite`
- `health_worker`: a separate process launched by the supervisor. It checks worker heartbeats, dead PIDs, stale jobs, failed jobs, and quote snapshot freshness.
- Worker loop profiling is sampled by `logging.profile_sample_seconds` to avoid writing profile rows on every idle poll.

Useful commands:

```bash
portfolio-bot health-check --config config.yaml --dry-run
portfolio-bot health-status --config config.yaml
portfolio-bot profile-suite --config config.yaml --iterations 3
portfolio-bot profile-report --config config.yaml
portfolio-bot runtime-logs --config config.yaml --level ERROR
```

`profile-suite` runs the core paths several times in dry-run mode by default: realtime scan, deep scan, report generation, paper snapshot/review, backtest, memory search, runtime status, and worker status. Use `--live` only when you intentionally want live side effects.

## Memory

The canonical memory store is local SQLite at `.portfolio_bot/memory.sqlite`.
It records strategy scores, market events, news/X leads, event analyses, and daily report summaries.

Useful commands:

```bash
portfolio-bot memory-add --config config.yaml --symbol POET "POET call looked cheap before catalyst confirmation"
portfolio-bot memory-search --config config.yaml "cheap long call catalyst"
portfolio-bot memory-recent --config config.yaml --symbol POET
portfolio-bot memory-status --config config.yaml
```

Optional open-source memory bridge:

```bash
pip install -e ".[memory]"
```

Then set `memory.open_source_enabled: true` in `config.yaml`. SQLite remains the source of truth.

## Paper trading and backtests

Paper trading writes only to `.portfolio_bot/paper_portfolio.sqlite`.
Every simulated order requires `strategy_name`, `strategy_version`, `signal_id`, and `reason`.
Options use a 100x multiplier by default.

Useful commands:

```bash
portfolio-bot paper-buy --config config.yaml POET_2027-01-15_13C --asset-type option --quantity 1 --price 3.5 --strategy-name semiconductor_reversal --signal-id sig-001 --reason "cheap long call candidate"
portfolio-bot paper-sell --config config.yaml POET_2027-01-15_13C --asset-type option --quantity 1 --price 4.2 --strategy-name semiconductor_reversal --signal-id sig-002 --reason "trim simulated risk"
portfolio-bot paper-positions --config config.yaml
portfolio-bot paper-equity-curve --config config.yaml
portfolio-bot paper-review --config config.yaml
```

Backtests write to `.portfolio_bot/backtest.sqlite` and are also copied into memory as `backtest_result`.
The v1 equity backtest is intentionally simple; the v1 long-call backtest uses a conservative payoff model until historical option chains are configured.

## Chinese reports

User-facing reports are Chinese by default. `report-now --dry-run` and `research-now --dry-run` still work when LLM quota or data API keys are unavailable, and they clearly separate:

- real holdings from `holdings.yaml`
- local paper portfolio and orders
- active/candidate/paused/retired strategy skill state
- recent backtests and review memory
- long-call and semiconductor-chain research candidates

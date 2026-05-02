from datetime import timedelta

from portfolio_bot.config import load_config
from portfolio_bot.notifications import AgentMailNotifier, IMessageNotifier, allow_notification_send, dedupe_repeated_lines, semantic_notification_key, select_notifiers, split_message


def test_split_message_keeps_short_message_intact():
    assert split_message("hello", 10) == ["hello"]


def test_split_message_chunks_long_lines():
    chunks = split_message("a" * 25, 10)
    assert chunks == ["a" * 10, "a" * 10, "a" * 5]


class DummyConfig:
    pass


def test_agentmail_prefers_email_for_long_reports():
    imessage = IMessageNotifier("user@example.com")
    agentmail = AgentMailNotifier(DummyConfig())

    selected = select_notifiers([imessage, agentmail], "Portfolio bot daily semiconductor report", "正文" * 1000)

    assert selected == [agentmail]


def test_agentmail_keeps_imessage_for_short_alerts():
    imessage = IMessageNotifier("user@example.com")
    agentmail = AgentMailNotifier(DummyConfig())

    selected = select_notifiers([imessage, agentmail], "POET 大行情", "短快讯")

    assert selected == [imessage]


def test_agentmail_sends_only_one_email_even_if_multiple_email_channels_exist():
    first = AgentMailNotifier(DummyConfig())
    second = AgentMailNotifier(DummyConfig())
    imessage = IMessageNotifier("user@example.com")

    selected = select_notifiers([imessage, first, second], "Portfolio bot daily semiconductor report", "正文" * 1000)

    assert selected == [first]


def test_dedupe_repeated_lines_keeps_at_most_three_copies():
    body = "\n".join(["same line"] * 5 + ["different"])

    assert dedupe_repeated_lines(body).splitlines() == ["same line", "same line", "same line", "different"]


def test_notification_repeat_limit_allows_three_sends_per_window(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )
    config = load_config(config_path)

    allowed = [
        allow_notification_send(config, "imessage", "subject", "same body", window=timedelta(hours=24))
        for _ in range(4)
    ]

    assert allowed == [True, True, True, False]


def test_semantic_notification_key_collapses_repriced_same_event():
    first = semantic_notification_key("email", "Portfolio daily report", "SNXX moved +15.45% to $116.99 after SNDK earnings.")
    second = semantic_notification_key("email", "Portfolio daily report", "SNXX moved +15.10% to $115.20 after SNDK earnings.")

    assert first == second

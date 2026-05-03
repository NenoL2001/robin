from __future__ import annotations

from pathlib import Path


def test_agent_layer_does_not_import_ingest_or_raw_document():
    for path in Path("robin/agent").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "RawDocument" not in text
        assert "robin.ingest" not in text


def test_no_raw_news_direct_to_prompt_builder():
    text = Path("robin/agent/analyzer.py").read_text(encoding="utf-8")

    assert "RAW_NEWS_KEYS" in text
    assert "assert_no_raw_news" in text


def test_ops_are_registered_without_hidden_global_data_reads():
    text = Path("robin/features/ops/builtins.py").read_text(encoding="utf-8")

    assert "open(" not in text
    assert "requests." not in text
    assert "httpx." not in text

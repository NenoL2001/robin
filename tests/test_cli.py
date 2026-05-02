from portfolio_bot.cli import main


def test_cli_paper_buy_positions_and_backtest(tmp_path, capsys):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        """
data_dir: data
holdings_path: holdings.yaml
analysts_path: analysts.yaml
strategy_root: strategy_skills
memory:
  enabled: true
notifications:
  imessage_enabled: false
""",
        encoding="utf-8",
    )

    main(
        [
            "--config",
            str(config_path),
            "paper-buy",
            "AEHR",
            "--quantity",
            "2",
            "--price",
            "10",
            "--strategy-name",
            "semiconductor_reversal",
            "--signal-id",
            "sig-cli",
            "--reason",
            "test buy",
        ]
    )
    assert '"symbol": "AEHR"' in capsys.readouterr().out

    main(["--config", str(config_path), "paper-positions"])
    assert "模拟净值" in capsys.readouterr().out

    main(["--config", str(config_path), "backtest", "--prices", "10,11,10,12"])
    assert "回测 semiconductor_reversal" in capsys.readouterr().out

    main(["--config", str(config_path), "runtime-status"])
    assert "runtime.sqlite" in capsys.readouterr().out

    main(["--config", str(config_path), "worker", "paper", "--once"])
    capsys.readouterr()

    main(["--config", str(config_path), "worker", "health", "--once", "--dry-run"])
    capsys.readouterr()

    main(["--config", str(config_path), "workers-status"])
    assert "paper" in capsys.readouterr().out

    main(["--config", str(config_path), "profile-suite", "--iterations", "1"])
    assert "Profile suite 完成" in capsys.readouterr().out

    main(["--config", str(config_path), "profile-report"])
    assert "scan_once" in capsys.readouterr().out

    main(["--config", str(config_path), "health-check", "--dry-run"])
    assert "系统健康" in capsys.readouterr().out

    main(["--config", str(config_path), "runtime-logs", "--limit", "1"])
    capsys.readouterr()

    main(["--config", str(config_path), "research-now", "AEHR", "--dry-run"])
    assert "主动新闻整理与公司分析" in capsys.readouterr().out

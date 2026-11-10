import json
import logging
import sys

import pytest

from alpha_atlas import cli
from alpha_atlas.terminal import terminal_progress


@pytest.mark.parametrize("quiet", [False, True])
def test_cli_progress_log_keeps_json_stdout_and_respects_quiet(
    tmp_path, monkeypatch, capsys, quiet
):
    logger = logging.getLogger("alpha_atlas.progress")
    monkeypatch.setattr(logger, "handlers", list(logger.handlers))
    monkeypatch.setattr(logger, "level", logger.level)
    monkeypatch.setattr(logger, "propagate", logger.propagate)

    def search(*args, **kwargs):
        terminal_progress("搜索开始", 资产="合成数据")
        return tmp_path / "run"

    monkeypatch.setattr("alpha_atlas.runner.run", search)
    argv = [
        "atlas",
        "--root",
        str(tmp_path),
        "run",
        "--asset",
        "ashare",
        "--fold",
        "fold1",
        "--search-only",
    ]
    if quiet:
        argv.append("--quiet")
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()

    output = capsys.readouterr()
    assert json.loads(output.out) == {"run_dir": str(tmp_path / "run")}
    logs = list((tmp_path / "logs" / "cli").glob("*.log"))
    if quiet:
        assert not output.err
        assert not logs
    else:
        assert "搜索开始" in output.err
        assert len(logs) == 1
        assert "搜索开始" in logs[0].read_text(encoding="utf-8")

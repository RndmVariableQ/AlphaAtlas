import json
import logging
import sys
from pathlib import Path

import pytest

from alpha_atlas import cli
from alpha_atlas.methods import make_method
from alpha_atlas.versions import load_version


@pytest.mark.parametrize("config_flag", ["--config", "--version"])
@pytest.mark.parametrize("explicit_method", [False, True])
def test_common_cli_accepts_recorded_v3_residual_configuration(
    monkeypatch, capsys, config_flag, explicit_method
):
    root = Path(__file__).resolve().parents[1]
    name = "rq8_fold2_v3async_luna_p8_pe8_px3_avail_nodepth_residual_v10"
    calls = []
    logger = logging.getLogger("alpha_atlas.progress")
    monkeypatch.setattr(logger, "handlers", list(logger.handlers))
    monkeypatch.setattr(logger, "level", logger.level)
    monkeypatch.setattr(logger, "propagate", logger.propagate)

    def search(*args, **kwargs):
        calls.append((args, kwargs))
        return root / "synthetic-run"

    monkeypatch.setattr("alpha_atlas.runner.run", search)
    argv = [
        "atlas",
        "--root",
        str(root),
        "run",
        config_flag,
        name,
        "--seed",
        "42",
        "--attempts",
        "1000",
        "--search-only",
        "--quiet",
    ]
    if explicit_method:
        argv.extend(["--method", "v3_async"])
    monkeypatch.setattr(sys, "argv", argv)
    cli.main()
    assert json.loads(capsys.readouterr().out) == {"run_dir": str(root / "synthetic-run")}
    assert calls[0][0][1:6] == ("futures_kernel_5m_full", "fold2", "v3_async", 42, 1000)
    assert calls[0][1]["version"] == name
    version = load_version(root, name)
    method = make_method("v3_async", 42, version["method_configs"]["v3_async"])
    assert version["rules"]["library_residual"] == "residual_ic_v1"
    assert version["rules"]["min_residual_ic"] == 0.0
    assert method.config.scale_bands == "intraday_1d_20d"
    assert (method.config.parallel_plans, method.evaluation_slots, method.explorer_slots) == (
        8,
        8,
        3,
    )


def test_common_cli_requires_method_for_multi_method_configuration(monkeypatch, capsys):
    monkeypatch.setattr(
        "alpha_atlas.versions.load_version",
        lambda root, name: {
            "asset": "synthetic",
            "fold": {"id": "fold2"},
            "method_configs": {"random": {}, "gp": {}},
        },
    )
    monkeypatch.setattr(sys, "argv", ["atlas", "run", "--config", "multi", "--quiet"])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert "specify --method" in capsys.readouterr().err

"""Tests for the openflight-cloud CLI argument wiring."""

import sys

import pytest

from openflight.cloud import cli, trigger
from openflight.cloud.config import CloudConfig


class TestArgParsing:
    def test_requires_subcommand(self, capsys):
        rc = cli.main([])
        assert rc != 0

    def test_unknown_subcommand_errors(self):
        with pytest.raises(SystemExit):
            cli.main(["frobnicate"])


class TestDispatch:
    def test_status_dispatches(self, monkeypatch, tmp_path):
        called = {}

        def fake_status(config, log_dir, client=None, out=print):
            called["log_dir"] = log_dir
            out("status-ran")
            return {}

        monkeypatch.setattr(cli.commands, "cmd_status", fake_status)
        rc = cli.main(["status", "--log-dir", str(tmp_path), "--config", str(tmp_path / "c.json")])
        assert rc == 0
        assert called["log_dir"] == tmp_path

    def test_push_passes_dry_run_flag(self, monkeypatch, tmp_path):
        captured = {}

        def fake_push(config, log_dir, client, dry_run=False, retry=False, session=None, out=print):
            captured["dry_run"] = dry_run
            return {"needs_relink": False}

        monkeypatch.setattr(cli.commands, "cmd_push", fake_push)
        cli.main(
            ["push", "--dry-run", "--log-dir", str(tmp_path), "--config", str(tmp_path / "c.json")]
        )
        assert captured["dry_run"] is True

    def test_push_retry_all(self, monkeypatch, tmp_path):
        captured = {}

        def fake_push(config, log_dir, client, dry_run=False, retry=False, session=None, out=print):
            captured.update(retry=retry, session=session)
            return {"needs_relink": False}

        monkeypatch.setattr(cli.commands, "cmd_push", fake_push)
        cli.main(
            ["push", "--retry", "--log-dir", str(tmp_path), "--config", str(tmp_path / "c.json")]
        )
        assert captured == {"retry": True, "session": None}

    def test_push_retry_named_session(self, monkeypatch, tmp_path):
        captured = {}

        def fake_push(config, log_dir, client, dry_run=False, retry=False, session=None, out=print):
            captured.update(retry=retry, session=session)
            return {"needs_relink": False}

        monkeypatch.setattr(cli.commands, "cmd_push", fake_push)
        cli.main(
            [
                "push",
                "--retry",
                "session_20260527",
                "--log-dir",
                str(tmp_path),
                "--config",
                str(tmp_path / "c.json"),
            ]
        )
        assert captured == {"retry": True, "session": "session_20260527"}

    def test_push_no_retry_by_default(self, monkeypatch, tmp_path):
        captured = {}

        def fake_push(config, log_dir, client, dry_run=False, retry=False, session=None, out=print):
            captured.update(retry=retry, session=session)
            return {"needs_relink": False}

        monkeypatch.setattr(cli.commands, "cmd_push", fake_push)
        cli.main(["push", "--log-dir", str(tmp_path), "--config", str(tmp_path / "c.json")])
        assert captured == {"retry": False, "session": None}

    def test_push_returns_nonzero_when_relink_needed(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.commands, "cmd_push", lambda *a, **k: {"needs_relink": True})
        rc = cli.main(["push", "--log-dir", str(tmp_path), "--config", str(tmp_path / "c.json")])
        assert rc != 0

    def test_link_dispatches(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.commands, "cmd_link", lambda *a, **k: True)
        rc = cli.main(["link", "--config", str(tmp_path / "c.json")])
        assert rc == 0

    def test_link_returns_nonzero_on_failure(self, monkeypatch, tmp_path):
        monkeypatch.setattr(cli.commands, "cmd_link", lambda *a, **k: False)
        rc = cli.main(["link", "--config", str(tmp_path / "c.json")])
        assert rc != 0


class TestSharedOptions:
    """--config/--log-dir must work before or after the subcommand.

    fire_push_async (cloud/trigger.py) runs ``--config X --log-dir Y push``.
    """

    def _run_push(self, monkeypatch, argv):
        captured = {}

        def fake_load_config(path):
            captured["config"] = path

        def fake_push(config, log_dir, client, dry_run=False, retry=False, session=None, out=print):
            captured["log_dir"] = log_dir
            return {"needs_relink": False}

        monkeypatch.setattr(cli, "load_config", fake_load_config)
        monkeypatch.setattr(cli.commands, "cmd_push", fake_push)
        assert cli.main(argv) == 0
        return captured

    @pytest.mark.parametrize("before_subcommand", [True, False])
    def test_options_reach_push(self, monkeypatch, tmp_path, before_subcommand):
        opts = ["--config", str(tmp_path / "c.json"), "--log-dir", str(tmp_path)]
        argv = opts + ["push"] if before_subcommand else ["push"] + opts
        captured = self._run_push(monkeypatch, argv)
        assert captured == {"config": tmp_path / "c.json", "log_dir": tmp_path}

    def test_session_end_push_command(self, monkeypatch, tmp_path):
        calls = []
        trigger.fire_push_async(
            CloudConfig(device_token="t", device_id="i", enabled=True),
            log_dir=tmp_path,
            config_path=tmp_path / "c.json",
            popen_fn=lambda cmd, **k: calls.append(cmd),
        )
        assert calls[0][:3] == [sys.executable, "-m", "openflight.cloud.cli"]
        argv = calls[0][3:]
        captured = self._run_push(monkeypatch, argv)
        assert captured == {"config": tmp_path / "c.json", "log_dir": tmp_path}

    def test_defaults_without_options(self, monkeypatch):
        captured = self._run_push(monkeypatch, ["push"])
        assert captured == {"config": cli.CONFIG_PATH, "log_dir": cli.DEFAULT_LOG_DIR}

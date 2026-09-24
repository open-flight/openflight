"""Tests for the openflight-cloud config module."""

import json
import os
import stat

import pytest

from openflight.cloud import config as cfg


class TestLoadConfig:
    def test_returns_none_when_file_absent(self, tmp_path):
        assert cfg.load_config(tmp_path / "cloud.json") is None

    def test_loads_all_fields(self, tmp_path):
        path = tmp_path / "cloud.json"
        path.write_text(
            json.dumps(
                {
                    "endpoint": "https://example.test",
                    "device_token": "of_device_" + "a" * 32,
                    "device_id": "abc-123",
                    "enabled": True,
                }
            )
        )
        loaded = cfg.load_config(path)
        assert loaded.endpoint == "https://example.test"
        assert loaded.device_token == "of_device_" + "a" * 32
        assert loaded.device_id == "abc-123"
        assert loaded.enabled is True

    def test_defaults_endpoint_and_enabled_when_missing(self, tmp_path):
        path = tmp_path / "cloud.json"
        path.write_text(json.dumps({"device_token": "t", "device_id": "i"}))
        loaded = cfg.load_config(path)
        assert loaded.endpoint == cfg.DEFAULT_ENDPOINT
        assert loaded.enabled is True


class TestSaveConfig:
    @pytest.mark.skipif(
        os.name == "nt", reason="POSIX permission bits are not representable on Windows"
    )
    def test_writes_file_with_0600_permissions(self, tmp_path):
        path = tmp_path / "nested" / "cloud.json"
        config = cfg.CloudConfig(device_token="tok", device_id="id")
        cfg.save_config(config, path)

        assert path.exists()
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600

    def test_round_trips_through_load(self, tmp_path):
        path = tmp_path / "cloud.json"
        config = cfg.CloudConfig(
            endpoint="https://e.test",
            device_token="of_device_" + "b" * 32,
            device_id="dev-9",
            enabled=False,
        )
        cfg.save_config(config, path)
        loaded = cfg.load_config(path)
        assert loaded == config


class TestIsLinked:
    def test_true_when_token_and_id_present(self):
        config = cfg.CloudConfig(device_token="t", device_id="i")
        assert config.is_linked()

    def test_false_when_token_missing(self):
        assert not cfg.CloudConfig(device_id="i").is_linked()

    def test_false_when_id_missing(self):
        assert not cfg.CloudConfig(device_token="t").is_linked()


class TestIsActive:
    def test_active_requires_enabled_and_linked(self):
        assert cfg.CloudConfig(device_token="t", device_id="i", enabled=True).is_active()

    def test_inactive_when_disabled(self):
        assert not cfg.CloudConfig(device_token="t", device_id="i", enabled=False).is_active()

    def test_inactive_when_not_linked(self):
        assert not cfg.CloudConfig(enabled=True).is_active()

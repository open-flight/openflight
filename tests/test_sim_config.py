"""Tests for sim.config — config/sim.json parsing (enabled connectors only)."""
import json

import pytest

from openflight.sim.config import load_sim_config


def _write(tmp_path, obj):
    p = tmp_path / "sim.json"
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def test_missing_file_means_no_connectors(tmp_path):
    assert load_sim_config(config_path=tmp_path / "absent.json") == []


def test_file_enabled_connector_loaded(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": True, "host": "10.0.0.5", "port": 921},
    ]})
    cfgs = load_sim_config(config_path=p)
    assert len(cfgs) == 1
    assert cfgs[0].type == "gspro"
    assert cfgs[0].host == "10.0.0.5"
    assert cfgs[0].units == "Yards"  # per-type default


def test_disabled_connectors_excluded(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": False, "port": 921},
        {"type": "opengolfsim", "enabled": True},
    ]})
    cfgs = load_sim_config(config_path=p)
    assert [c.type for c in cfgs] == ["opengolfsim"]


def test_gspro_default_port(tmp_path):
    p = _write(tmp_path, {"connectors": [{"type": "gspro", "enabled": True}]})
    assert load_sim_config(config_path=p)[0].port == 921


def test_opengolfsim_default_port(tmp_path):
    # OGS is reached on its Developer API port (3111).
    p = _write(tmp_path, {"connectors": [{"type": "opengolfsim", "enabled": True}]})
    assert load_sim_config(config_path=p)[0].port == 3111


def test_partee_default_port(tmp_path):
    p = _write(tmp_path, {"connectors": [{"type": "partee", "enabled": True}]})
    assert load_sim_config(config_path=p)[0].port == 921


def test_explicit_port_overrides_default(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "opengolfsim", "enabled": True, "host": "192.168.1.9", "port": 9000},
    ]})
    cfg = load_sim_config(config_path=p)[0]
    assert cfg.host == "192.168.1.9" and cfg.port == 9000


def test_multiple_connectors_both_enabled(tmp_path):
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": True},
        {"type": "opengolfsim", "enabled": True},
    ]})
    assert {c.type for c in load_sim_config(config_path=p)} == {"gspro", "opengolfsim"}


def test_unknown_type_in_file_raises(tmp_path):
    p = _write(tmp_path, {"connectors": [{"type": "bogus", "port": 1}]})
    with pytest.raises(ValueError):
        load_sim_config(config_path=p)


def test_malformed_json_degrades_to_empty(tmp_path):
    """A syntactically broken sim.json must not crash startup — sim is opt-in and
    the core shot pipeline doesn't depend on it (PR #115 review #4)."""
    p = tmp_path / "sim.json"
    p.write_text("{ this is not valid json", encoding="utf-8")
    assert load_sim_config(config_path=p) == []


def test_wrong_top_level_shape_degrades_to_empty(tmp_path):
    p = _write(tmp_path, ["not", "an", "object"])
    assert load_sim_config(config_path=p) == []


def test_unreadable_path_degrades_to_empty(tmp_path):
    # A directory at the config path: read_text raises IsADirectoryError (OSError).
    p = tmp_path / "sim.json"
    p.mkdir()
    assert load_sim_config(config_path=p) == []


def test_malformed_connector_entry_skipped_others_kept(tmp_path):
    """One broken connector (non-int port) is skipped with a warning, not allowed
    to drop the valid ones (PR #115 review #4)."""
    p = _write(tmp_path, {"connectors": [
        {"type": "gspro", "enabled": True, "port": "not-a-number"},
        {"type": "opengolfsim", "enabled": True, "port": 3111},
    ]})
    assert [c.type for c in load_sim_config(config_path=p)] == ["opengolfsim"]

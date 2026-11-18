"""OPS243 port forwarding from the interactive setup script."""

import re
import subprocess
from pathlib import Path

SETUP = Path(__file__).parents[1] / "scripts" / "setup" / "setup.sh"


def test_setup_accepts_and_forwards_ops_port_to_both_phases():
    script = SETUP.read_text(encoding="utf-8")

    assert "--ops-port <path>" in script
    assert re.search(r"--ops-port\).*?OPS_PORT=\"\$2\"", script, re.DOTALL)
    assert "--ops-port requires a device path" in script
    assert 'OPS_ARGS=(--port "$OPS_PORT")' in script
    assert script.count('test_rolling_buffer_persist.py --setup "${OPS_ARGS[@]}"') == 1
    assert script.count('test_rolling_buffer_persist.py --test "${OPS_ARGS[@]}"') == 1


def test_setup_help_documents_ops_port():
    result = subprocess.run(["bash", SETUP, "--help"], check=True, capture_output=True, text=True)

    assert "--ops-port <path>" in result.stdout


def test_setup_rejects_ops_port_without_a_path():
    result = subprocess.run(
        ["bash", SETUP, "--ops-port"], check=False, capture_output=True, text=True
    )

    assert result.returncode == 1
    assert "--ops-port requires a device path" in result.stdout


def test_setup_script_has_valid_bash_syntax():
    subprocess.run(["bash", "-n", SETUP], check=True)

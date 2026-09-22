"""Tests for packaging metadata that affects setup/install behavior."""

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 compatibility
    import tomli as tomllib


def _pyproject() -> dict:
    return tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))


def _requirement_name(dependency: str) -> str:
    """Extract the distribution name from a PEP 508 dependency string."""
    return re.split(r"[<>=!~ \[;]", dependency, maxsplit=1)[0].strip()


def test_kld7_is_installed_by_default():
    """K-LD7 driver must be a base dependency so every install path includes it.

    The package is a tiny pure-Python wheel whose only requirement (pyserial) is
    already a base dependency, and the --kld7 runtime flag gates actual hardware
    use. Shipping it by default avoids the recurring "kld7 package not installed"
    failure on clean installs, since setup.sh, `uv sync`, and CI do not pull
    optional extras.
    """
    dependencies = _pyproject()["project"]["dependencies"]

    assert any(_requirement_name(dep) == "kld7" for dep in dependencies)


def test_camera_dependencies_are_not_installed_by_default():
    """Camera-only packages should not be part of the base install."""
    dependencies = _pyproject()["project"]["dependencies"]

    assert not any(_requirement_name(dep) == "opencv-python-headless" for dep in dependencies)
    assert not any(dep.startswith("trackers ") for dep in dependencies)
    assert not any(dep.startswith("supervision") for dep in dependencies)


def test_camera_extra_installs_portable_image_processing_dependency():
    """OpenCV is opt-in while Picamera2 remains an OS-managed Pi package."""
    camera_dependencies = _pyproject()["project"]["optional-dependencies"]["camera"]

    assert any(_requirement_name(dep) == "opencv-python-headless" for dep in camera_dependencies)
    assert not any(_requirement_name(dep) == "picamera2" for dep in camera_dependencies)

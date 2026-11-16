"""Validation and persistence for phone-assisted radar orientation calibration."""

from __future__ import annotations

import json
import math
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


class PhoneOrientationValidationError(ValueError):
    """A phone orientation payload is unsafe or internally inconsistent."""


def _finite_number(payload: dict, key: str) -> float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PhoneOrientationValidationError(f"{key} must be a number")
    number = float(value)
    if not math.isfinite(number):
        raise PhoneOrientationValidationError(f"{key} must be finite")
    return number


@dataclass(frozen=True)
class PhoneOrientationMeasurement:
    """One stable, gravity-referenced phone measurement."""

    schema_version: int
    mount_tilt_deg: float
    roll_deg: float
    gravity_x_g: float
    gravity_y_g: float
    gravity_z_g: float
    tilt_stddev_deg: float
    roll_stddev_deg: float
    sample_count: int
    measured_at: str
    device_model: str

    @classmethod
    def from_payload(cls, payload: Any) -> "PhoneOrientationMeasurement":
        """Validate a client payload and recompute its angles from gravity."""
        if not isinstance(payload, dict):
            raise PhoneOrientationValidationError("JSON body must be an object")
        if payload.get("schema_version") != 1:
            raise PhoneOrientationValidationError("schema_version must be 1")

        sample_count = payload.get("sample_count")
        if isinstance(sample_count, bool) or not isinstance(sample_count, int):
            raise PhoneOrientationValidationError("sample_count must be an integer")
        if sample_count < 30:
            raise PhoneOrientationValidationError("at least 30 samples are required")

        tilt_stddev = _finite_number(payload, "tilt_stddev_deg")
        roll_stddev = _finite_number(payload, "roll_stddev_deg")
        if not 0.0 <= tilt_stddev <= 0.5 or not 0.0 <= roll_stddev <= 0.5:
            raise PhoneOrientationValidationError(
                "phone must remain stable (angle standard deviation must be at most 0.5 degrees)"
            )

        gravity_x = _finite_number(payload, "gravity_x_g")
        gravity_y = _finite_number(payload, "gravity_y_g")
        gravity_z = _finite_number(payload, "gravity_z_g")
        gravity_norm = math.sqrt(
            gravity_x * gravity_x + gravity_y * gravity_y + gravity_z * gravity_z
        )
        if not 0.9 <= gravity_norm <= 1.1:
            raise PhoneOrientationValidationError("gravity vector must be between 0.9g and 1.1g")

        recomputed_tilt = math.degrees(math.asin(max(-1.0, min(1.0, -gravity_z / gravity_norm))))
        recomputed_roll = math.degrees(math.atan2(gravity_x, -gravity_y))
        submitted_tilt = _finite_number(payload, "mount_tilt_deg")
        submitted_roll = _finite_number(payload, "roll_deg")
        if abs(submitted_tilt - recomputed_tilt) > 0.25:
            raise PhoneOrientationValidationError(
                "mount_tilt_deg does not match the transmitted gravity vector"
            )
        if abs(submitted_roll - recomputed_roll) > 0.25:
            raise PhoneOrientationValidationError(
                "roll_deg does not match the transmitted gravity vector"
            )
        if not -30.0 <= recomputed_tilt <= 45.0:
            raise PhoneOrientationValidationError("mount tilt must be between -30 and 45 degrees")
        if abs(recomputed_roll) > 3.0:
            raise PhoneOrientationValidationError(
                "level the radar left-to-right within 3 degrees before calibrating"
            )

        measured_at = payload.get("measured_at")
        if not isinstance(measured_at, str) or not measured_at.strip():
            raise PhoneOrientationValidationError("measured_at must be an ISO-8601 timestamp")
        device_model = payload.get("device_model", "iPhone")
        if not isinstance(device_model, str):
            raise PhoneOrientationValidationError("device_model must be a string")

        return cls(
            schema_version=1,
            mount_tilt_deg=recomputed_tilt,
            roll_deg=recomputed_roll,
            gravity_x_g=gravity_x,
            gravity_y_g=gravity_y,
            gravity_z_g=gravity_z,
            tilt_stddev_deg=tilt_stddev,
            roll_stddev_deg=roll_stddev,
            sample_count=sample_count,
            measured_at=measured_at.strip()[:64],
            device_model=device_model.strip()[:80] or "iPhone",
        )

    def to_dict(self) -> dict:
        """Return a rounded JSON-safe representation."""
        data = asdict(self)
        for key in (
            "mount_tilt_deg",
            "roll_deg",
            "tilt_stddev_deg",
            "roll_stddev_deg",
        ):
            data[key] = round(data[key], 4)
        for key in ("gravity_x_g", "gravity_y_g", "gravity_z_g"):
            data[key] = round(data[key], 6)
        return data


def save_phone_orientation_calibration(record: dict, path: Path) -> None:
    """Atomically persist a validated applied-orientation record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(record, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary_path = Path(handle.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def load_phone_orientation_calibration(path: Path) -> dict | None:
    """Load and validate the persisted applied-orientation record."""
    path = Path(path)
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as handle:
        record = json.load(handle)
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise PhoneOrientationValidationError("saved calibration schema_version must be 1")
    configured_tilt = _finite_number(record, "configured_iwr_tilt_deg")
    if not -45.0 <= configured_tilt <= 45.0:
        raise PhoneOrientationValidationError("saved configured IWR tilt is out of range")
    measurement = PhoneOrientationMeasurement.from_payload(record.get("measurement"))
    normalized = dict(record)
    normalized["configured_iwr_tilt_deg"] = configured_tilt
    normalized["measurement"] = measurement.to_dict()
    return normalized


__all__ = [
    "PhoneOrientationMeasurement",
    "PhoneOrientationValidationError",
    "load_phone_orientation_calibration",
    "save_phone_orientation_calibration",
]

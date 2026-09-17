"""Validate the ballistic model against paired OpenFlight + TrackMan data.

Two independent comparisons are produced:

1. **TM-inputs** — feed TrackMan's measured (ball_speed, launch_v,
   launch_h, spin_rpm, spin_axis) into :func:`openflight.ballistics.simulate`
   and compare the model's ``carry_yards`` to TrackMan's ``Carry Flat -
   Length``. This isolates the physics model from any sensor noise.

2. **OF-inputs** — feed OpenFlight's measured values for the same shots
   into the model and compare to TrackMan's carry. This is the
   end-to-end check the user actually sees on screen (sensor + model
   error combined). Spin handling matches production
   :func:`openflight.ballistics.resolve_launch`: measured spin if
   confidence >= SPIN_CONFIDENCE_HIGH, otherwise club-typical fallback.
   OpenFlight has no spin-axis measurement, so spin_axis = 0 is assumed.

TrackMan's "Flat" carry is normalized to no-wind, 0 ft altitude, 77 °F.
We pass ``air_density = 1.184 kg/m³`` (≈ 25 °C sea level) so the model
runs in the same atmosphere TrackMan reports for — otherwise the default
1.225 kg/m³ (15 °C ISA) would add a systematic ~2 yd model bias on
driver-distance shots.

Usage (single session)::

    uv run python scripts/analysis/validate_ballistics.py \\
        --trackman session_logs/OpenFlight-Test.Normalized.csv \\
        --comparison session_logs/comparison_20260506.csv \\
        --output-dir session_logs/validation_20260506

Usage (multiple sessions — concatenates shots, tags each with a session
label, and emits per-session stats alongside the per-club stats)::

    uv run python scripts/analysis/validate_ballistics.py \\
        --trackman session_logs/tm_session_a.csv session_logs/tm_session_b.csv \\
        --comparison session_logs/comp_a.csv session_logs/comp_b.csv \\
        --session-label may06 jun02 \\
        --output-dir session_logs/validation_multi

``--comparison`` (and ``--session-label``) must match the count of
``--trackman`` when provided. When ``--comparison`` is omitted the
OF-inputs run is skipped.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Make the in-repo package importable when running via `uv run python ...`
# from the repo root (uv usually arranges this, but we also support a
# plain `python scripts/analysis/validate_ballistics.py` invocation).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

from openflight.ballistics import (  # noqa: E402
    CLUB_TYPICAL_SPIN_RPM,
    LaunchConditions,
    simulate,
)
from openflight.clubs import ClubType  # noqa: E402
from openflight.launch_monitor import SPIN_CONFIDENCE_HIGH  # noqa: E402

# TrackMan "Flat" normalization: no wind, 0 ft altitude, 77 °F.
# ρ = P / (R_specific · T) with P = 101325 Pa, T = 298.15 K, R = 287.05 J/(kg·K)
# → 1.1839 kg/m³. We round to 1.184 for the input.
TM_FLAT_AIR_DENSITY = 1.184

# Club-name → ClubType map. The comparison CSV uses normalized names like
# "driver", "7-iron", "pw"; the raw TM CSV uses "7 Iron", "Driver", "PW".
_CLUB_MAP: Dict[str, ClubType] = {
    "driver": ClubType.DRIVER,
    "3-wood": ClubType.WOOD_3,
    "5-wood": ClubType.WOOD_5,
    "7-wood": ClubType.WOOD_7,
    "3-hybrid": ClubType.HYBRID_3,
    "5-hybrid": ClubType.HYBRID_5,
    "7-hybrid": ClubType.HYBRID_7,
    "9-hybrid": ClubType.HYBRID_9,
    "2-iron": ClubType.IRON_2,
    "3-iron": ClubType.IRON_3,
    "4-iron": ClubType.IRON_4,
    "5-iron": ClubType.IRON_5,
    "6-iron": ClubType.IRON_6,
    "7-iron": ClubType.IRON_7,
    "8-iron": ClubType.IRON_8,
    "9-iron": ClubType.IRON_9,
    "pw": ClubType.PW,
    "gw": ClubType.GW,
    "sw": ClubType.SW,
    "lw": ClubType.LW,
}


def _normalize_club(raw: Optional[str]) -> str:
    """Lower-case, collapse "7 Iron" / "7-iron" / "iron 7" → "7-iron" etc."""
    if raw is None:
        return ""
    s = str(raw).strip().lower().replace("_", "-")
    if not s:
        return ""
    # Strip trailing punctuation.
    s = s.rstrip(".")
    # "7 iron" → "7-iron", "iron 7" → "7-iron"
    parts = s.replace("-", " ").split()
    if len(parts) == 2:
        a, b = parts
        if a.isdigit() and b in ("iron", "i", "wood", "w", "hybrid", "h"):
            kind = {"iron": "iron", "i": "iron", "wood": "wood", "w": "wood",
                    "hybrid": "hybrid", "h": "hybrid"}[b]
            return f"{a}-{kind}"
        if b.isdigit() and a in ("iron", "wood", "hybrid"):
            return f"{b}-{a}"
    aliases = {"drv": "driver", "1-wood": "driver", "pitching-wedge": "pw",
               "sand-wedge": "sw", "gap-wedge": "gw", "lob-wedge": "lw"}
    return aliases.get(s, s)


def _club_type(name: str) -> ClubType:
    return _CLUB_MAP.get(_normalize_club(name), ClubType.UNKNOWN)


def _to_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class TMShot:
    """Slim TrackMan record carrying only what the ballistic model needs."""

    shot_index: int  # row index in the CSV (1-based, after header/units rows)
    club_raw: str
    ball_speed_mph: Optional[float]
    launch_v_deg: Optional[float]
    launch_h_deg: Optional[float]
    spin_rpm: Optional[float]
    spin_axis_deg: Optional[float]
    carry_yards: Optional[float]
    timestamp: str
    session: str = ""  # session label (typically derived from the source filename)


def _default_session_label(path: Path) -> str:
    """Cheap session label: filename stem, with any "OpenFlight-Test."
    or "comparison_" prefix trimmed so output is readable."""
    stem = path.stem
    for prefix in ("OpenFlight-Test.", "comparison_", "session_"):
        if stem.startswith(prefix):
            stem = stem[len(prefix):]
            break
    return stem or path.name


def load_trackman(path: Path, session: Optional[str] = None) -> List[TMShot]:
    """Read a TrackMan "Normalized" CSV. Handles ``sep=,`` preamble and
    the units row directly under the header. Each emitted shot is tagged
    with ``session`` (defaulting to the file stem) so multi-session
    aggregations can keep their origin straight."""
    session_label = session if session is not None else _default_session_label(path)
    shots: List[TMShot] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        # Skip optional ``sep=,`` Excel preamble.
        pos = fh.tell()
        first = fh.readline().lstrip("﻿").lstrip()
        if not first.lower().startswith("sep="):
            fh.seek(pos)
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            return shots
        # Trackman exports sometimes include a units row ("[mph]", "[deg]")
        # right under the header. Detect by looking for "[..]" cells.
        idx = 0
        for row in reader:
            if any(
                isinstance(v, str) and v.strip().startswith("[") and v.strip().endswith("]")
                for v in row.values()
            ):
                continue
            idx += 1
            shots.append(TMShot(
                shot_index=idx,
                club_raw=row.get("Club", "") or "",
                ball_speed_mph=_to_float(row.get("Ball Speed")),
                launch_v_deg=_to_float(row.get("Launch Angle")),
                launch_h_deg=_to_float(row.get("Launch Direction")),
                spin_rpm=_to_float(row.get("Spin Rate")),
                spin_axis_deg=_to_float(row.get("Spin Axis")),
                carry_yards=_to_float(row.get("Carry Flat - Length")),
                timestamp=row.get("Date", "") or "",
                session=session_label,
            ))
    return shots


@dataclass
class ComparisonRow:
    """One row from the paired OF/TM comparison CSV."""

    timestamp_of: str
    club_raw: str
    match_quality: str
    # OpenFlight fields
    ball_speed_of: Optional[float]
    launch_v_of: Optional[float]
    launch_h_of: Optional[float]
    spin_of: Optional[float]
    carry_of: Optional[float]
    # TrackMan fields (for ground truth and fallback spin)
    ball_speed_tm: Optional[float]
    launch_v_tm: Optional[float]
    launch_h_tm: Optional[float]
    spin_tm: Optional[float]
    spin_axis_tm: Optional[float]  # may stay None — comparison CSV usually omits axis
    carry_tm: Optional[float]
    session: str = ""


def load_comparison(path: Path, session: Optional[str] = None) -> List[ComparisonRow]:
    session_label = session if session is not None else _default_session_label(path)
    rows: List[ComparisonRow] = []
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for r in reader:
            rows.append(ComparisonRow(
                timestamp_of=r.get("timestamp_of", "") or "",
                club_raw=r.get("club", "") or "",
                match_quality=r.get("match_quality", "") or "",
                ball_speed_of=_to_float(r.get("ball_speed_of")),
                launch_v_of=_to_float(r.get("launch_v_of")),
                launch_h_of=_to_float(r.get("launch_h_of")),
                spin_of=_to_float(r.get("spin_of")),
                carry_of=_to_float(r.get("carry_of")),
                ball_speed_tm=_to_float(r.get("ball_speed_tm")),
                launch_v_tm=_to_float(r.get("launch_v_tm")),
                launch_h_tm=_to_float(r.get("launch_h_tm")),
                spin_tm=_to_float(r.get("spin_tm")),
                spin_axis_tm=_to_float(r.get("spin_axis_tm")),
                carry_tm=_to_float(r.get("carry_tm")),
                session=session_label,
            ))
    return rows


def _spin_axis_lookup(tm_shots: List[TMShot]) -> Dict[Tuple[str, float, float], float]:
    """Build (club, ball_speed_rounded, launch_v_rounded) -> spin_axis_deg
    so comparison rows (which may lack spin_axis) can borrow TM's axis."""
    out: Dict[Tuple[str, float, float], float] = {}
    for s in tm_shots:
        if s.spin_axis_deg is None or s.ball_speed_mph is None or s.launch_v_deg is None:
            continue
        club = _normalize_club(s.club_raw)
        key = (club, round(s.ball_speed_mph, 1), round(s.launch_v_deg, 1))
        out.setdefault(key, s.spin_axis_deg)
    return out


@dataclass
class ValidationRow:
    """One row of the per-shot validation CSV."""

    source: str  # "tm" or "of"
    shot_label: str
    club: str
    session: str
    ball_speed_mph: float
    launch_v_deg: float
    launch_h_deg: float
    spin_rpm: float
    spin_axis_deg: float
    spin_source: str  # "measured" / "club_typical" / "tm_borrowed"
    measured_carry_yards: float
    model_carry_yards: float
    delta_yards: float  # model - measured


def _has_required_inputs(*vals) -> bool:
    return all(v is not None and not (isinstance(v, float) and math.isnan(v)) for v in vals)


def validate_tm_inputs(
    tm_shots: List[TMShot],
    air_density: float = TM_FLAT_AIR_DENSITY,
) -> List[ValidationRow]:
    """Run the model on each TrackMan shot and compare to TM's flat carry."""
    out: List[ValidationRow] = []
    for s in tm_shots:
        if not _has_required_inputs(
            s.ball_speed_mph, s.launch_v_deg, s.spin_rpm, s.carry_yards
        ):
            continue
        spin_axis = s.spin_axis_deg if s.spin_axis_deg is not None else 0.0
        launch_h = s.launch_h_deg if s.launch_h_deg is not None else 0.0
        conditions = LaunchConditions(
            ball_speed_mph=s.ball_speed_mph,
            launch_angle_v=s.launch_v_deg,
            launch_angle_h=launch_h,
            spin_rpm=s.spin_rpm,
            spin_axis_deg=spin_axis,
            spin_source="measured",
        )
        traj = simulate(conditions, air_density=air_density)
        session_tag = s.session or "default"
        out.append(ValidationRow(
            source="tm",
            shot_label=f"tm#{s.shot_index}@{session_tag}",
            club=_normalize_club(s.club_raw),
            session=session_tag,
            ball_speed_mph=s.ball_speed_mph,
            launch_v_deg=s.launch_v_deg,
            launch_h_deg=launch_h,
            spin_rpm=s.spin_rpm,
            spin_axis_deg=spin_axis,
            spin_source="measured",
            measured_carry_yards=s.carry_yards,
            model_carry_yards=traj.carry_yards,
            delta_yards=traj.carry_yards - s.carry_yards,
        ))
    return out


def _resolve_of_spin(
    row: ComparisonRow,
    club: ClubType,
) -> Tuple[float, str]:
    """Mirror production resolve_launch(): use OF spin only when confident,
    else club-typical fallback. The comparison CSV doesn't carry spin
    confidence, so we treat any present OF spin as measured (this is the
    same fallback the production server would log)."""
    if row.spin_of is not None and row.spin_of > 0:
        return row.spin_of, "measured"
    typical = CLUB_TYPICAL_SPIN_RPM.get(club, CLUB_TYPICAL_SPIN_RPM[ClubType.UNKNOWN])
    return typical, "club_typical"


def validate_of_inputs(
    comp_rows: List[ComparisonRow],
    spin_axis_lookup: Dict[Tuple[str, float, float], float],
    air_density: float = TM_FLAT_AIR_DENSITY,
) -> List[ValidationRow]:
    """Run the model on OpenFlight inputs and compare to TrackMan's
    flat carry. Skips rows flagged as ``ball_speed_mismatch`` etc."""
    out: List[ValidationRow] = []
    for i, row in enumerate(comp_rows, start=1):
        if row.match_quality != "good":
            continue
        if not _has_required_inputs(
            row.ball_speed_of, row.launch_v_of, row.carry_tm
        ):
            continue
        club_enum = _club_type(row.club_raw)
        spin_rpm, spin_source = _resolve_of_spin(row, club_enum)
        # OF doesn't measure spin axis. Borrow TM's axis when we can
        # match the shot; otherwise assume pure backspin (axis = 0).
        spin_axis = 0.0
        if row.ball_speed_tm is not None and row.launch_v_tm is not None:
            key = (
                _normalize_club(row.club_raw),
                round(row.ball_speed_tm, 1),
                round(row.launch_v_tm, 1),
            )
            if key in spin_axis_lookup:
                spin_axis = spin_axis_lookup[key]
        launch_h = row.launch_h_of if row.launch_h_of is not None else 0.0
        conditions = LaunchConditions(
            ball_speed_mph=row.ball_speed_of,
            launch_angle_v=row.launch_v_of,
            launch_angle_h=launch_h,
            spin_rpm=spin_rpm,
            spin_axis_deg=spin_axis,
            spin_source="measured" if spin_source == "measured" else "club_typical",
        )
        traj = simulate(conditions, air_density=air_density)
        session_tag = row.session or "default"
        out.append(ValidationRow(
            source="of",
            shot_label=f"of#{i}@{session_tag}@{row.timestamp_of[:19]}",
            club=_normalize_club(row.club_raw),
            session=session_tag,
            ball_speed_mph=row.ball_speed_of,
            launch_v_deg=row.launch_v_of,
            launch_h_deg=launch_h,
            spin_rpm=spin_rpm,
            spin_axis_deg=spin_axis,
            spin_source=spin_source,
            measured_carry_yards=row.carry_tm,
            model_carry_yards=traj.carry_yards,
            delta_yards=traj.carry_yards - row.carry_tm,
        ))
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_per_shot_csv(rows: List[ValidationRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "source", "session", "shot_label", "club",
            "ball_speed_mph", "launch_v_deg", "launch_h_deg",
            "spin_rpm", "spin_axis_deg", "spin_source",
            "measured_carry_yards", "model_carry_yards",
            "delta_yards", "abs_delta_yards",
        ])
        for r in rows:
            w.writerow([
                r.source, r.session, r.shot_label, r.club,
                f"{r.ball_speed_mph:.2f}", f"{r.launch_v_deg:.2f}", f"{r.launch_h_deg:.2f}",
                f"{r.spin_rpm:.0f}", f"{r.spin_axis_deg:.2f}", r.spin_source,
                f"{r.measured_carry_yards:.2f}", f"{r.model_carry_yards:.2f}",
                f"{r.delta_yards:.2f}", f"{abs(r.delta_yards):.2f}",
            ])


def _stats(deltas: List[float]) -> Dict[str, float]:
    if not deltas:
        return {"n": 0, "mean": float("nan"), "stdev": float("nan"),
                "rmse": float("nan"), "mae": float("nan"), "max_abs": float("nan")}
    mean = statistics.fmean(deltas)
    stdev = statistics.pstdev(deltas) if len(deltas) > 1 else 0.0
    rmse = math.sqrt(sum(d * d for d in deltas) / len(deltas))
    mae = sum(abs(d) for d in deltas) / len(deltas)
    return {
        "n": len(deltas),
        "mean": mean,
        "stdev": stdev,
        "rmse": rmse,
        "mae": mae,
        "max_abs": max(abs(d) for d in deltas),
    }


def format_stats(label: str, rows: List[ValidationRow]) -> str:
    """Return a multi-line stats block: overall, per-club, per-session."""
    lines = [f"=== {label} ==="]
    overall = _stats([r.delta_yards for r in rows])
    lines.append(
        f"OVERALL  n={overall['n']:3d}  bias={overall['mean']:+6.2f} yd  "
        f"rmse={overall['rmse']:5.2f} yd  mae={overall['mae']:5.2f} yd  "
        f"max|d|={overall['max_abs']:5.2f} yd"
    )

    # Per-club table
    clubs = sorted({r.club for r in rows})
    lines.append("-" * 80)
    lines.append("By club:")
    lines.append(f"  {'club':10s}  {'n':>3s}  {'bias':>8s}  {'rmse':>6s}  "
                 f"{'mae':>6s}  {'max|d|':>7s}  {'measured_n':>4s}  {'fallback_n':>4s}")
    for club in clubs:
        club_rows = [r for r in rows if r.club == club]
        s = _stats([r.delta_yards for r in club_rows])
        measured_n = sum(1 for r in club_rows if r.spin_source == "measured")
        fallback_n = sum(1 for r in club_rows if r.spin_source == "club_typical")
        lines.append(
            f"  {club:10s}  {s['n']:3d}  {s['mean']:+7.2f}  {s['rmse']:6.2f}  "
            f"{s['mae']:6.2f}  {s['max_abs']:7.2f}  {measured_n:>10d}  {fallback_n:>10d}"
        )

    # Per-session table — only shown when there's more than one session.
    sessions = sorted({r.session for r in rows})
    if len(sessions) > 1:
        lines.append("By session:")
        lines.append(f"  {'session':24s}  {'n':>3s}  {'bias':>8s}  {'rmse':>6s}  "
                     f"{'mae':>6s}  {'max|d|':>7s}")
        for sess in sessions:
            sess_rows = [r for r in rows if r.session == sess]
            s = _stats([r.delta_yards for r in sess_rows])
            sess_disp = sess if len(sess) <= 24 else sess[:21] + "..."
            lines.append(
                f"  {sess_disp:24s}  {s['n']:3d}  {s['mean']:+7.2f}  {s['rmse']:6.2f}  "
                f"{s['mae']:6.2f}  {s['max_abs']:7.2f}"
            )
    lines.append("")
    return "\n".join(lines)


def write_scatter(
    rows: List[ValidationRow],
    title: str,
    out_path: Path,
) -> None:
    """Predicted-vs-measured scatter with 1:1 line and ±5 yd band."""
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print(f"[warn] matplotlib not available — skipping {out_path.name}",
              file=sys.stderr)
        return

    if not rows:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Color by club, marker by session (so multi-session runs are legible).
    clubs = sorted({r.club for r in rows})
    sessions = sorted({r.session for r in rows})
    color_for = {c: plt.cm.tab10(i % 10) for i, c in enumerate(clubs)}
    # Reused matplotlib markers — cycle if there are many sessions.
    marker_cycle = ["o", "s", "^", "D", "v", "P", "X", "*", "<", ">"]
    marker_for = {s: marker_cycle[i % len(marker_cycle)] for i, s in enumerate(sessions)}

    fig, ax = plt.subplots(figsize=(7, 7))
    for club in clubs:
        for sess in sessions:
            cr = [r for r in rows if r.club == club and r.session == sess]
            if not cr:
                continue
            label = (
                f"{club} (n={len(cr)})" if len(sessions) == 1
                else f"{club}/{sess} (n={len(cr)})"
            )
            ax.scatter(
                [r.measured_carry_yards for r in cr],
                [r.model_carry_yards for r in cr],
                label=label,
                color=color_for[club],
                marker=marker_for[sess],
                s=42, alpha=0.85, edgecolor="k", linewidth=0.5,
            )
    all_x = [r.measured_carry_yards for r in rows]
    all_y = [r.model_carry_yards for r in rows]
    lo = min(min(all_x), min(all_y)) * 0.95
    hi = max(max(all_x), max(all_y)) * 1.05
    ax.plot([lo, hi], [lo, hi], "k--", lw=1, label="1:1")
    ax.fill_between([lo, hi], [lo - 5, hi - 5], [lo + 5, hi + 5],
                    color="grey", alpha=0.15, label="+/-5 yd")

    stats = _stats([r.delta_yards for r in rows])
    ax.set_title(
        f"{title}\nn={stats['n']}  bias={stats['mean']:+.2f} yd  "
        f"rmse={stats['rmse']:.2f} yd  mae={stats['mae']:.2f} yd"
    )
    ax.set_xlabel("Measured carry (TrackMan Flat, yd)")
    ax.set_ylabel("Model carry (yd)")
    ax.set_xlim(lo, hi)
    ax.set_ylim(lo, hi)
    ax.set_aspect("equal")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    # Allow non-ASCII (e.g. arrows, Greek letters) in printed stats on
    # Windows consoles whose default encoding is cp1252.
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trackman", required=True, type=Path, nargs="+",
                        help="One or more TrackMan normalized CSVs (each "
                             "contains Spin Axis column). Multiple files "
                             "are concatenated; each shot is tagged with a "
                             "session label derived from the filename "
                             "(override with --session-label).")
    parser.add_argument("--comparison", required=False, type=Path, nargs="+",
                        help="Paired OF/TM CSV(s) from compare_trackman.py "
                             "(used for OF-inputs validation). When passing "
                             "multiple, count must match --trackman and "
                             "files are paired by position so the spin-axis "
                             "lookup uses the matching session's TM data.")
    parser.add_argument("--session-label", required=False, type=str, nargs="+",
                        help="Optional explicit labels (one per --trackman). "
                             "Default = filename stem with prefixes trimmed.")
    parser.add_argument("--output-dir", required=True, type=Path,
                        help="Directory for per-shot CSVs, stats.txt, scatter PNGs.")
    parser.add_argument("--air-density", type=float, default=TM_FLAT_AIR_DENSITY,
                        help=f"Air density (kg/m³) for simulate(). Default "
                             f"{TM_FLAT_AIR_DENSITY} matches TrackMan 'Flat' (77°F sea level).")
    args = parser.parse_args(argv)

    # Resolve session labels.
    if args.session_label and len(args.session_label) != len(args.trackman):
        print(
            f"--session-label count ({len(args.session_label)}) must match "
            f"--trackman count ({len(args.trackman)})",
            file=sys.stderr,
        )
        return 2
    labels = args.session_label or [_default_session_label(p) for p in args.trackman]

    # Validate input files exist.
    for p in args.trackman:
        if not p.exists():
            print(f"TrackMan CSV not found: {p}", file=sys.stderr)
            return 2
    if args.comparison:
        if len(args.comparison) != len(args.trackman):
            print(
                f"--comparison count ({len(args.comparison)}) must match "
                f"--trackman count ({len(args.trackman)})",
                file=sys.stderr,
            )
            return 2
        for p in args.comparison:
            if not p.exists():
                print(f"Comparison CSV not found: {p}", file=sys.stderr)
                return 2

    # Load all TM shots and all comparison rows, tagged with session.
    all_tm_shots: List[TMShot] = []
    per_session_tm: List[List[TMShot]] = []  # for per-session axis lookup
    for path, label in zip(args.trackman, labels):
        shots = load_trackman(path, session=label)
        print(f"Loaded {len(shots)} TM shots from {path.name} [{label}]")
        all_tm_shots.extend(shots)
        per_session_tm.append(shots)

    # --- TM inputs ---
    tm_rows = validate_tm_inputs(all_tm_shots, air_density=args.air_density)
    write_per_shot_csv(tm_rows, args.output_dir / "validation_tm_inputs.csv")
    write_scatter(
        tm_rows,
        f"TM-inputs validation (rho={args.air_density:.3f} kg/m³)",
        args.output_dir / "scatter_tm_inputs.png",
    )
    stats_text = [format_stats(
        f"TM-inputs (model fed TrackMan measurements, rho={args.air_density:.3f})",
        tm_rows,
    )]

    # --- OF inputs ---
    of_rows: List[ValidationRow] = []
    if args.comparison:
        all_comp_rows: List[ComparisonRow] = []
        for comp_path, label, tm_subset in zip(args.comparison, labels, per_session_tm):
            comp = load_comparison(comp_path, session=label)
            print(f"Loaded {len(comp)} paired rows from {comp_path.name} [{label}]")
            # Build axis lookup from this session's TM data — keeps the
            # mapping unambiguous when different sessions share ball speeds.
            axis_lookup = _spin_axis_lookup(tm_subset)
            of_rows.extend(validate_of_inputs(
                comp, axis_lookup, air_density=args.air_density,
            ))
            all_comp_rows.extend(comp)
        write_per_shot_csv(of_rows, args.output_dir / "validation_of_inputs.csv")
        write_scatter(
            of_rows,
            f"OF-inputs end-to-end (rho={args.air_density:.3f} kg/m³)",
            args.output_dir / "scatter_of_inputs.png",
        )
        stats_text.append(format_stats(
            f"OF-inputs (OpenFlight measurements → model → TM carry, rho={args.air_density:.3f})",
            of_rows,
        ))

    out_stats = args.output_dir / "stats.txt"
    out_stats.parent.mkdir(parents=True, exist_ok=True)
    out_stats.write_text("\n".join(stats_text), encoding="utf-8")

    # Echo to stdout so the test runner immediately sees the result.
    print()
    print("\n".join(stats_text))
    print(f"\nWrote {out_stats}")
    print(f"Wrote {args.output_dir / 'validation_tm_inputs.csv'}")
    if of_rows:
        print(f"Wrote {args.output_dir / 'validation_of_inputs.csv'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

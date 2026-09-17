"""Tune the six polynomial aerodynamic coefficients in
:mod:`openflight.ballistics` against TrackMan-measured carry.

Parameters swept (Cd = a + b*Sp + c*Sp^2, Cl = d + e*Sp + f*Sp^2):
    CD_POLY = (a, b, c)
    CL_POLY = (d, e, f)

Method: scipy.optimize.differential_evolution (global) followed by
Nelder-Mead refinement. Loss is overall RMSE on the TrackMan-inputs
shots (model fed TM's own measurements vs TM's flat carry).

Air density is set to 1.184 kg/m³ to match TrackMan's "Flat"
normalization (no wind, 0 ft alt, 77 °F).

Usage (single session)::

    uv run python scripts/analysis/sweep_ballistic_coeffs.py \\
        --trackman session_logs/OpenFlight-Test.Normalized.csv \\
        --output-dir session_logs/sweep_20260506

Usage (multiple sessions, with leave-one-session-out CV)::

    uv run python scripts/analysis/sweep_ballistic_coeffs.py \\
        --trackman session_logs/tm_a.csv session_logs/tm_b.csv \\
        --session-label may06 jun02 \\
        --loso \\
        --output-dir session_logs/sweep_multi

Multi-session shots are concatenated for the main fit. ``--loso`` then
refits on N-1 sessions per fold and scores on the held-out one — if the
held-out RMSE is much worse than the fit RMSE, the optimizer is
absorbing session-specific noise and the coefficients won't generalize.

The script does not modify ballistics.py — it prints the optimal
coefficients and writes a stats/scatter report so the change can be
reviewed before being committed.
"""

from __future__ import annotations

import argparse
import csv
import math
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
from scipy.optimize import differential_evolution, minimize

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))
if str(_REPO_ROOT / "scripts" / "analysis") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "scripts" / "analysis"))

import openflight.ballistics as bl  # noqa: E402
from openflight.ballistics import LaunchConditions, simulate  # noqa: E402

# Reuse the TM CSV loader from the validation script so the data path is
# identical between the two tools.
from validate_ballistics import (  # noqa: E402
    TM_FLAT_AIR_DENSITY,
    TMShot,
    _default_session_label,
    _normalize_club,
    load_trackman,
)


# Bounds for the search. Wide enough to span published Cd/Cl ranges for
# dimpled golf balls in the post-drag-crisis regime; narrow enough that
# differential evolution converges in a few minutes.
PARAM_BOUNDS: List[Tuple[float, float]] = [
    (0.08, 0.30),  # Cd a: drag at zero spin
    (0.00, 1.80),  # Cd b
    (-1.80, 0.00),  # Cd c
    (0.00, 0.15),  # Cl d
    (0.40, 2.20),  # Cl e
    (-2.20, -0.30),  # Cl f: negative so lift peaks and turns over
]
PARAM_NAMES = ["cd_a", "cd_b", "cd_c", "cl_d", "cl_e", "cl_f"]
Coeffs = Tuple[float, ...]
DEFAULT_COEFFS: Coeffs = (*bl.CD_POLY, *bl.CL_POLY)


@dataclass
class FitResult:
    coeffs: Coeffs
    rmse: float
    preds: List[float]


def _build_conditions(s: TMShot) -> LaunchConditions:
    return LaunchConditions(
        ball_speed_mph=s.ball_speed_mph,
        launch_angle_v=s.launch_v_deg,
        launch_angle_h=s.launch_h_deg if s.launch_h_deg is not None else 0.0,
        spin_rpm=s.spin_rpm,
        spin_axis_deg=s.spin_axis_deg if s.spin_axis_deg is not None else 0.0,
        spin_source="measured",
    )


def _filter_shots(shots: List[TMShot]) -> List[TMShot]:
    """Keep only shots with the inputs the model needs + a measured carry."""
    out = []
    for s in shots:
        if s.ball_speed_mph is None or s.launch_v_deg is None:
            continue
        if s.spin_rpm is None or s.carry_yards is None:
            continue
        out.append(s)
    return out


def simulate_with_coeffs(
    shots: List[TMShot],
    coeffs: Coeffs,
) -> List[float]:
    """Monkey-patch the ballistics module constants, run simulate() for
    every shot, restore the originals on exit.

    Relies on ``ballistics._cd`` and ``ballistics._cl`` resolving
    ``CD_POLY``/``CL_POLY`` at call time from the module's global namespace.
    """
    saved = (bl.CD_POLY, bl.CL_POLY)
    bl.CD_POLY, bl.CL_POLY = tuple(coeffs[:3]), tuple(coeffs[3:])
    try:
        results = []
        for s in shots:
            traj = simulate(_build_conditions(s), air_density=TM_FLAT_AIR_DENSITY)
            results.append(traj.carry_yards)
        return results
    finally:
        bl.CD_POLY, bl.CL_POLY = saved


def make_loss(shots: List[TMShot], measured: np.ndarray):
    """Closure that the optimizer can call with a 6-vector."""
    def _loss(x: np.ndarray) -> float:
        preds = simulate_with_coeffs(shots, tuple(x))
        return float(np.sqrt(np.mean((np.asarray(preds) - measured) ** 2)))
    return _loss


def evaluate(
    shots: List[TMShot],
    measured: np.ndarray,
    coeffs: Coeffs,
) -> FitResult:
    preds = simulate_with_coeffs(shots, coeffs)
    rmse = float(np.sqrt(np.mean((np.asarray(preds) - measured) ** 2)))
    return FitResult(coeffs=coeffs, rmse=rmse, preds=preds)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _stats(deltas: List[float]) -> Dict[str, float]:
    if not deltas:
        return {"n": 0, "mean": float("nan"), "stdev": float("nan"),
                "rmse": float("nan"), "mae": float("nan"), "max_abs": float("nan")}
    mean = statistics.fmean(deltas)
    stdev = statistics.pstdev(deltas) if len(deltas) > 1 else 0.0
    rmse = math.sqrt(sum(d * d for d in deltas) / len(deltas))
    mae = sum(abs(d) for d in deltas) / len(deltas)
    return {"n": len(deltas), "mean": mean, "stdev": stdev,
            "rmse": rmse, "mae": mae, "max_abs": max(abs(d) for d in deltas)}


def format_table(
    label: str,
    shots: List[TMShot],
    measured: List[float],
    preds: List[float],
) -> str:
    """Stats block grouped by club + (when multiple) by session."""
    by_club: Dict[str, List[float]] = {}
    by_session: Dict[str, List[float]] = {}
    for s, m, p in zip(shots, measured, preds):
        delta = p - m
        by_club.setdefault(_normalize_club(s.club_raw), []).append(delta)
        by_session.setdefault(s.session or "default", []).append(delta)

    overall = _stats([p - m for p, m in zip(preds, measured)])
    lines = [f"=== {label} ==="]
    lines.append(
        f"OVERALL  n={overall['n']:3d}  bias={overall['mean']:+6.2f} yd  "
        f"rmse={overall['rmse']:5.2f} yd  mae={overall['mae']:5.2f} yd  "
        f"max|d|={overall['max_abs']:5.2f} yd"
    )
    lines.append("By club:")
    lines.append(f"  {'club':14s}  {'n':>3s}  {'bias':>8s}  {'rmse':>6s}  "
                 f"{'mae':>6s}  {'max|d|':>7s}")
    for club in sorted(by_club):
        s = _stats(by_club[club])
        lines.append(
            f"  {club:14s}  {s['n']:3d}  {s['mean']:+7.2f}  {s['rmse']:6.2f}  "
            f"{s['mae']:6.2f}  {s['max_abs']:7.2f}"
        )
    if len(by_session) > 1:
        lines.append("By session:")
        lines.append(f"  {'session':24s}  {'n':>3s}  {'bias':>8s}  {'rmse':>6s}  "
                     f"{'mae':>6s}  {'max|d|':>7s}")
        for sess in sorted(by_session):
            s = _stats(by_session[sess])
            sess_disp = sess if len(sess) <= 24 else sess[:21] + "..."
            lines.append(
                f"  {sess_disp:24s}  {s['n']:3d}  {s['mean']:+7.2f}  {s['rmse']:6.2f}  "
                f"{s['mae']:6.2f}  {s['max_abs']:7.2f}"
            )
    return "\n".join(lines)


def write_per_shot_csv(
    shots: List[TMShot],
    measured: List[float],
    preds_default: List[float],
    preds_fit: List[float],
    out_path: Path,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow([
            "session", "shot_index", "club",
            "ball_speed_mph", "launch_v_deg", "launch_h_deg",
            "spin_rpm", "spin_axis_deg",
            "measured_carry_yards",
            "model_default_yards", "delta_default",
            "model_fit_yards", "delta_fit",
        ])
        for s, m, pd_, pf in zip(shots, measured, preds_default, preds_fit):
            w.writerow([
                s.session or "default",
                s.shot_index, _normalize_club(s.club_raw),
                f"{s.ball_speed_mph:.2f}", f"{s.launch_v_deg:.2f}",
                f"{(s.launch_h_deg or 0.0):.2f}",
                f"{s.spin_rpm:.0f}", f"{(s.spin_axis_deg or 0.0):.2f}",
                f"{m:.2f}",
                f"{pd_:.2f}", f"{pd_ - m:+.2f}",
                f"{pf:.2f}", f"{pf - m:+.2f}",
            ])


def write_scatter(
    shots: List[TMShot],
    measured: List[float],
    preds_default: List[float],
    preds_fit: List[float],
    out_path: Path,
    fit_coeffs: Coeffs,
) -> None:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_path.parent.mkdir(parents=True, exist_ok=True)
    clubs = sorted({_normalize_club(s.club_raw) for s in shots})
    sessions = sorted({(s.session or "default") for s in shots})
    color_for = {c: plt.cm.tab10(i % 10) for i, c in enumerate(clubs)}
    marker_cycle = ["o", "s", "^", "D", "v", "P", "X", "*", "<", ">"]
    marker_for = {s: marker_cycle[i % len(marker_cycle)] for i, s in enumerate(sessions)}
    single_session = len(sessions) == 1

    fig, axes = plt.subplots(1, 2, figsize=(13, 6.5), sharex=True, sharey=True)

    for ax, preds, label in (
        (axes[0], preds_default, "default"),
        (axes[1], preds_fit, "fit"),
    ):
        for club in clubs:
            for sess in sessions:
                xs, ys = [], []
                for s, m, p in zip(shots, measured, preds):
                    if _normalize_club(s.club_raw) != club:
                        continue
                    if (s.session or "default") != sess:
                        continue
                    xs.append(m)
                    ys.append(p)
                if not xs:
                    continue
                leg_label = (
                    f"{club} (n={len(xs)})" if single_session
                    else f"{club}/{sess} (n={len(xs)})"
                )
                ax.scatter(xs, ys, label=leg_label,
                           color=color_for[club], marker=marker_for[sess],
                           s=42, alpha=0.85, edgecolor="k", linewidth=0.5)
        all_x = list(measured)
        all_y = preds
        lo = min(min(all_x), min(all_y)) * 0.95
        hi = max(max(all_x), max(all_y)) * 1.05
        ax.plot([lo, hi], [lo, hi], "k--", lw=1, label="1:1")
        ax.fill_between([lo, hi], [lo - 5, hi - 5], [lo + 5, hi + 5],
                        color="grey", alpha=0.15, label="+/-5 yd")
        deltas = np.asarray(preds) - np.asarray(measured)
        rmse = float(np.sqrt(np.mean(deltas ** 2)))
        bias = float(np.mean(deltas))
        ax.set_title(f"{label}: rmse={rmse:.2f} yd  bias={bias:+.2f} yd")
        ax.set_xlabel("Measured carry (TM Flat, yd)")
        ax.set_ylabel("Model carry (yd)")
        ax.set_xlim(lo, hi)
        ax.set_ylim(lo, hi)
        ax.set_aspect("equal")
        ax.grid(True, alpha=0.3)
        ax.legend(loc="best", fontsize=8)

    fig.suptitle(
        "Default vs fit (fit: "
        + ", ".join(f"{n}={v:.4f}" for n, v in zip(PARAM_NAMES, fit_coeffs))
        + ")",
        fontsize=11,
    )
    fig.tight_layout()
    fig.savefig(out_path, dpi=140)
    plt.close(fig)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trackman", required=True, type=Path, nargs="+",
                        help="One or more TrackMan normalized CSVs. Shots "
                             "from all files are concatenated for the fit.")
    parser.add_argument("--session-label", required=False, type=str, nargs="+",
                        help="Optional explicit session label per --trackman "
                             "file (default: filename stem with common "
                             "prefixes trimmed).")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=42,
                        help="DE seed for reproducibility.")
    parser.add_argument("--de-popsize", type=int, default=12,
                        help="Differential-evolution population size.")
    parser.add_argument("--de-maxiter", type=int, default=40,
                        help="Differential-evolution max iterations.")
    parser.add_argument("--de-tol", type=float, default=1e-3,
                        help="Differential-evolution tolerance.")
    parser.add_argument("--loso", action="store_true",
                        help="Leave-one-session-out cross-validation: for "
                             "each session, refit on the others and report "
                             "the held-out RMSE. Only meaningful with "
                             "multiple --trackman files.")
    args = parser.parse_args(argv)

    # Validate inputs.
    for p in args.trackman:
        if not p.exists():
            print(f"TrackMan CSV not found: {p}", file=sys.stderr)
            return 2
    if args.session_label and len(args.session_label) != len(args.trackman):
        print(
            f"--session-label count ({len(args.session_label)}) must match "
            f"--trackman count ({len(args.trackman)})",
            file=sys.stderr,
        )
        return 2

    # Load all sessions, tagged with their labels.
    labels = args.session_label or [_default_session_label(p) for p in args.trackman]
    all_shots: List[TMShot] = []
    for path, label in zip(args.trackman, labels):
        shots_in = load_trackman(path, session=label)
        usable = _filter_shots(shots_in)
        print(f"Loaded {len(shots_in)} TM shots from {path.name} [{label}] "
              f"({len(usable)} usable)")
        all_shots.extend(usable)
    shots = all_shots
    if not shots:
        print("No usable shots loaded.", file=sys.stderr)
        return 2
    measured = np.array([s.carry_yards for s in shots], dtype=float)
    print(f"Total usable shots across {len(args.trackman)} session(s): {len(shots)}")

    # Baseline with current ballistics.py constants.
    baseline = evaluate(shots, measured, DEFAULT_COEFFS)
    print(f"Baseline RMSE: {baseline.rmse:.3f} yd  "
          f"(coeffs: {DEFAULT_COEFFS})")

    loss_fn = make_loss(shots, measured)

    # --- Global search: differential evolution ---
    eval_counter = {"n": 0}

    def _logged_loss(x):
        eval_counter["n"] += 1
        return loss_fn(x)

    print(f"\nDifferential evolution: popsize={args.de_popsize}, "
          f"maxiter={args.de_maxiter}, seed={args.seed}")
    t0 = time.time()
    de_result = differential_evolution(
        _logged_loss,
        bounds=PARAM_BOUNDS,
        seed=args.seed,
        maxiter=args.de_maxiter,
        popsize=args.de_popsize,
        tol=args.de_tol,
        polish=False,  # we do our own Nelder-Mead refinement
        updating="deferred",  # vectorizable, but more importantly deterministic w/ seed
        workers=1,
        disp=False,
    )
    de_elapsed = time.time() - t0
    print(f"DE converged in {eval_counter['n']} evals "
          f"({de_elapsed:.1f}s). Best RMSE: {de_result.fun:.3f} yd")
    print(f"  -> {dict(zip(PARAM_NAMES, de_result.x))}")

    # --- Local refinement: Nelder-Mead from DE best ---
    print("\nNelder-Mead refinement...")
    t1 = time.time()
    nm = minimize(
        loss_fn,
        x0=de_result.x,
        method="Nelder-Mead",
        bounds=PARAM_BOUNDS,
        options={"xatol": 1e-5, "fatol": 1e-4, "maxiter": 400},
    )
    nm_elapsed = time.time() - t1
    print(f"NM finished in {nm.nit} iterations ({nm_elapsed:.1f}s). "
          f"RMSE: {nm.fun:.3f} yd")

    # Pick whichever is better (NM should be ≤ DE).
    if nm.fun <= de_result.fun:
        best_x = tuple(float(v) for v in nm.x)
        best_rmse = float(nm.fun)
    else:
        best_x = tuple(float(v) for v in de_result.x)
        best_rmse = float(de_result.fun)
    fit = evaluate(shots, measured, best_x)
    assert abs(fit.rmse - best_rmse) < 1e-6, (fit.rmse, best_rmse)

    # --- Report ---
    args.output_dir.mkdir(parents=True, exist_ok=True)

    table_default = format_table(
        "Default coefficients",
        shots, list(measured), baseline.preds,
    )
    table_fit = format_table(
        "Fit coefficients",
        shots, list(measured), fit.preds,
    )

    fit_lines = [
        "Optimal coefficients (TM-inputs, RMSE objective):",
        *(
            f"  {name:<5} = {fit:>9.5f}   (default {default:>9.5f})"
            for name, fit, default in zip(PARAM_NAMES, best_x, DEFAULT_COEFFS)
        ),
        f"  CD_POLY = ({best_x[0]:.4f}, {best_x[1]:.4f}, {best_x[2]:.4f})",
        f"  CL_POLY = ({best_x[3]:.4f}, {best_x[4]:.4f}, {best_x[5]:.4f})",
        "",
        f"Baseline RMSE: {baseline.rmse:.3f} yd",
        f"Fit RMSE:      {fit.rmse:.3f} yd",
        f"Improvement:   {baseline.rmse - fit.rmse:+.3f} yd "
        f"({100 * (baseline.rmse - fit.rmse) / baseline.rmse:+.1f}%)",
        "",
        table_default,
        "",
        table_fit,
    ]

    # --- Leave-one-session-out cross-validation ---
    # Only emit when --loso is requested AND there are multiple sessions.
    # This is the honest generalization check: refit on N-1 sessions, score
    # on the held-out one. If held-out RMSE is much worse than fit RMSE,
    # the optimizer is finding session-specific quirks (overfit).
    sessions_in_data = sorted({s.session or "default" for s in shots})
    if args.loso and len(sessions_in_data) > 1:
        loso_lines = ["", "=== Leave-one-session-out cross-validation ==="]
        loso_lines.append(
            f"{'held-out':24s}  {'n_test':>6s}  {'fit_RMSE':>9s}  "
            f"{'test_RMSE':>10s}  {'test_bias':>10s}  fit_coeffs"
        )
        for held_out in sessions_in_data:
            train_shots = [s for s in shots if (s.session or "default") != held_out]
            test_shots = [s for s in shots if (s.session or "default") == held_out]
            if not train_shots or not test_shots:
                continue
            train_measured = np.array([s.carry_yards for s in train_shots])
            test_measured = np.array([s.carry_yards for s in test_shots])
            print(f"\n  LOSO refit (held out: {held_out}, "
                  f"n_train={len(train_shots)}, n_test={len(test_shots)})...")
            sub_loss = make_loss(train_shots, train_measured)
            sub_de = differential_evolution(
                sub_loss,
                bounds=PARAM_BOUNDS,
                seed=args.seed,
                maxiter=args.de_maxiter,
                popsize=args.de_popsize,
                tol=args.de_tol,
                polish=False,
                updating="deferred",
                workers=1,
                disp=False,
            )
            sub_nm = minimize(
                sub_loss,
                x0=sub_de.x,
                method="Nelder-Mead",
                bounds=PARAM_BOUNDS,
                options={"xatol": 1e-5, "fatol": 1e-4, "maxiter": 400},
            )
            sub_best = tuple(float(v) for v in (
                sub_nm.x if sub_nm.fun <= sub_de.fun else sub_de.x
            ))
            sub_fit_rmse = float(min(sub_nm.fun, sub_de.fun))
            # Score on held-out session with the refit coeffs.
            test_preds = simulate_with_coeffs(test_shots, sub_best)
            test_deltas = np.asarray(test_preds) - test_measured
            test_rmse = float(np.sqrt(np.mean(test_deltas ** 2)))
            test_bias = float(np.mean(test_deltas))
            ho_disp = held_out if len(held_out) <= 24 else held_out[:21] + "..."
            loso_lines.append(
                f"{ho_disp:24s}  {len(test_shots):>6d}  {sub_fit_rmse:>9.3f}  "
                f"{test_rmse:>10.3f}  {test_bias:>+10.3f}  "
                "(" + ", ".join(f"{v:.3f}" for v in sub_best) + ")"
            )
        fit_lines.extend(loso_lines)
    elif args.loso:
        fit_lines.extend([
            "",
            "(LOSO requested but only one session loaded — skipped.)",
        ])
    out_stats = args.output_dir / "sweep_stats.txt"
    out_stats.write_text("\n".join(fit_lines), encoding="utf-8")
    print()
    print("\n".join(fit_lines))

    write_per_shot_csv(
        shots, list(measured), baseline.preds, fit.preds,
        args.output_dir / "sweep_per_shot.csv",
    )
    write_scatter(
        shots, list(measured), baseline.preds, fit.preds,
        args.output_dir / "sweep_scatter.png",
        best_x,
    )
    print(f"\nWrote {out_stats}")
    print(f"Wrote {args.output_dir / 'sweep_per_shot.csv'}")
    print(f"Wrote {args.output_dir / 'sweep_scatter.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# Dechirped-Sideband Spin Replay

Reference for `scripts/analysis/replay_spin_dechirp.py` — the offline test
bench for the next-generation spin estimator. Offline only; it never touches
production behavior. Tune here against truth data first, then port to
`rolling_buffer/processor.py` once the numbers prove out.

## Why this exists

A spinning golf ball's rotating asymmetries (seam, logo, dimples) modulate the
radar return, creating **sidebands** flanking the ball's Doppler tone, spaced
at exact multiples of the modulation frequency. Read the spacing → read the
spin. This is how commercial radar units (TrackMan US8845442, FlightScope
US9868044) measure spin.

The production detector instead FFTs the bandpassed **amplitude envelope**,
which only captures the weak AM component (1-5% modulation depth) and discards
the phase modulation carrying most of the signal energy.

The trap blocking the sideband approach directly: **ball deceleration**. Drag
at driver speeds chirps the Doppler carrier ~3-4 kHz/s, so over a 60 ms
capture the carrier — and every sideband — sweeps ~200 Hz. Sideband spacings
are 40-270 Hz (2500-8000 RPM), so a plain FFT smears the whole pattern into
one blurred lump. The chirp must be removed first.

## Pipeline

1. **Carrier tracking** (`track_carrier`) — short overlapping STFTs (256
   samples ≈ 8.5 ms, 2 ms hop) find the ball Doppler peak near the
   OPS243-expected frequency in each frame; a quadratic `f_d(t)` is fitted
   through the peaks. SNR collapse marks the end of the usable window (net
   impact), which the script reports as `usable_ms`.
2. **Dechirp** (`dechirp`) — multiply the raw I/Q by
   `exp(-j·2π·∫f_d(t)dt)` (the conjugate chirp). The decelerating carrier
   becomes a stationary tone at 0 Hz; sidebands land at exactly ±m·f_mod,
   coherent over the whole window.
3. **Harmonic comb search** (`sideband_spin`) — one large zero-padded FFT,
   then a comb over candidate modulation frequencies (33-400 Hz, 0.5 Hz
   steps). Each candidate scores `min(upper, lower)` sideband support at
   ±1f/±2f/±3f — real spin sidebands are symmetric around the carrier,
   noise usually isn't. Each sideband is normalized by its **local**
   neighborhood floor, not a global one: the dechirped carrier's skirt
   slopes steeply, and with a global floor the lowest candidate frequency
   always wins (this was the first-run failure mode).
4. **Harmonic-number disambiguation** — the comb finds the sideband
   *spacing*; spin is that spacing (1× logo/asymmetry modulation) or half
   of it (the seam's 2-fold symmetry modulates at 2×, the common case per
   FlightScope's patent). Sideband support at 1.5× the found spacing — an
   odd harmonic — proves the true fundamental is half the spacing. Both
   interpretations are emitted (`dechirp_rpm_1x` / `dechirp_rpm_2x`) plus
   the pick (`dechirp_rpm`, `dechirp_choice`).
5. **Scoring** — pairs captures with launch-monitor truth and prints
   coverage / MAE / median / within-10% for three estimators: production
   envelope, dechirped sidebands, and a **harmonic oracle** (the better of
   the 1×/2× interpretations per shot — the ceiling a perfect
   disambiguation rule could reach).

## Requirements

1. **Software** — a normal OpenFlight install (`./scripts/setup/setup.sh
   --deps-only`). Runs on any machine, no radar hardware needed.
2. **A session JSONL with shots** — produced automatically by any normal
   session. The needed `shot_detected` and `rolling_buffer_capture` (raw
   4096-sample I/Q) entries are logged by default; no special flags.
3. **Spin truth** — hit a session with OpenFlight running alongside a
   reference monitor (TrackMan etc.) that exports per-shot spin to CSV,
   then pair the two:

   ```bash
   uv run python scripts/analysis/compare_trackman.py \
       --openflight session_logs/session_YYYYMMDD_*.jsonl \
       --trackman ~/Downloads/TrackMan_export.csv \
       --output session_logs/comparison_mysession.csv
   ```

   Pairing is by club + chronological order with a ball-speed tolerance, so
   the two systems don't need identical shot counts. Hand-rolled CSVs from
   other monitors work if they have `shot_number_of`, `match_quality`,
   `spin_tm`, and `ball_speed_tm` columns.

## Dedicated OPS diagnostic capture

Use the dedicated capture tool when the normal session log is not enough to
explain missing or implausible spin. Stop the kiosk first so only one process
owns the OPS serial port, then run:

```bash
uv run --no-sync python scripts/hardware-test/capture_ops_spin.py \
    --club driver \
    --environment outdoor-range \
    --ball-label "range ball" \
    --radar-to-ball-ft 1.5 \
    --max-captures 20
```

The radar must already boot in persisted rolling-buffer mode. The tool uses the
production 30 ksps, `S#12` post-heavy capture by default and never writes radar
flash. Each output record is flushed immediately to an
`ops_spin_capture_*.jsonl` file under `~/openflight_sessions`.

The JSONL preserves:

- the verbatim OPS response and SHA-256 before parsing;
- partial or malformed dumps and acquisition errors;
- all raw I/Q samples and ADC clipping/dynamic-range statistics;
- standard and overlapping speed timelines;
- impact timing, production multitaper output, the envelope-FFT comparison,
  and every spin candidate;
- OPS clock sync, radar identity/state queries, transport speed, software
  revision, processor constants, and physical setup metadata.

Replay the complete session or plot one capture directly:

```bash
uv run --no-sync python scripts/analysis/replay_captures.py \
    ~/openflight_sessions/ops_spin_capture_YYYYMMDD_HHMMSS.jsonl

uv run --no-sync python scripts/analysis/plot_spin_debug.py \
    --log ~/openflight_sessions/ops_spin_capture_YYYYMMDD_HHMMSS.jsonl \
    --shot 1
```

Collect reference-monitor spin when possible. Raw OPS evidence can explain
clipping, fades, truncation, and estimator disagreement, but truth-paired spin
is what distinguishes a plausible wrong answer from a correct one.

## Running

```bash
uv run --no-sync python scripts/analysis/replay_spin_dechirp.py \
    --openflight session_logs/session_YYYYMMDD_HHMMSS.jsonl \
    --comparison session_logs/comparison_mysession.csv \
    --output session_logs/spin_dechirp_replay.csv
```

Prints the three-row summary and writes a per-shot CSV (both spin
interpretations, comb score, usable window, chirp rate, baseline result).
Under a minute for a ~60-shot session.

Notes:

- Only shots with `match_quality == "good"` and a truth spin value are
  scored; unmatched warm-up shots are dropped automatically.
- The estimator is research-grade: trust the summary table and
  high-comb-score shots, not per-shot `dechirp_rpm` as a final answer.

## Baseline results (2026-06-11, session_20260511 / comparison_test2, 61 shots)

| estimator | coverage | result |
|---|---|---|
| production envelope | 12/61 | median error ~3200 rpm |
| dechirp, comb score ≥ 5 | 11/61 | **~1.0% median error** (0.1-1.5% per shot) |
| dechirp + harmonic oracle | 61/61 | 18/61 within 10% |

Interpretation: the spin information is present in our raw captures and
dechirping recovers it at launch-monitor-grade precision when the comb locks.
The two failure buckets are both visible in the per-shot CSV:

1. **Harmonic mis-choice** — errors of exactly 2× or ½× where the oracle
   column shows the right answer was available. Improving the
   disambiguation rule is the highest-value next step.
2. **Low-score noise locks** — shots where no real sideband pattern exists
   and the comb picks noise. Needs a comb-score confidence gate before any
   value is reported.

## Next steps (in value order)

1. Strengthen 1×/2× disambiguation (use odd-harmonic evidence more
   aggressively; consider club-band plausibility only as a tiebreaker).
2. Add a comb-score gate and report "not measurable" below it.
3. Validate on a held-out session (different day/rig if possible) before
   porting anything to production. More truth-paired sessions from diverse
   rigs/nets/balls are the most valuable contribution here.
4. Long term: this estimator is what could lift the production low-band
   confidence cap (`SPIN_LOW_BAND_SUSPECT_MAX_RPM` in
   `rolling_buffer/processor.py`) — the envelope path cannot distinguish
   real ≤3100 RPM spin from red envelope noise in a single 136 ms capture,
   but coherent sidebands carry far more evidence.

## Related

- `docs/rolling_buffer_spin_detection.md` — production envelope detector
- `scripts/analysis/experiment_spin_windows.py` — earlier window experiments
  (shares the JSONL/CSV loaders this script imports)
- TrackMan US8845442 / EP1698380, FlightScope US9868044, Liu 2017
  ("A Micro-Doppler Modulation of Spin Projectile on CW Radar") — the
  sideband physics and harmonic-ambiguity math

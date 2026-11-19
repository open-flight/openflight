"""Confidence must come from evidence, never a literal.

Session 140533 showed 0.95 confidence on five estimates whose own channels
disagreed by 8-10 degrees. HLCMF-v0 coherence was 0.71-0.95 and was computed,
logged, and then thrown away in favour of 0.95.
"""

from openflight import server
from openflight.iwr6843 import lcmf
from openflight.iwr6843.lcmf import LCMFResult


def test_single_channel_states_score_identically_and_rank_below_corroborated():
    """Both single-channel states carry the same evidence -- exactly one
    channel's word for it -- and must score identically, ranking below any
    corroborated result:

    - lone component, no cross-check possible: only one component ever
      existed, so ``np.std`` of that one value is ``0.0`` (lcmf.py:708).
    - bad channel caught and discarded: a real disagreement existed and was
      resolved by dropping the outlier, so ``component_std_deg`` reflects
      that (now-resolved) disagreement, not the confidence of what remains.

    Reusing ``component_std_deg`` as a spread penalty in either case either
    reads a coincidental 0.0 as "perfect agreement" (worse than honest) or
    double-counts a fault ``SINGLE_CHANNEL_CONFIDENCE_FACTOR`` already
    derates for (worse than the first case) -- so a lone component must not
    outrank a recovered one, or vice versa.
    """
    corroborated = server.vertical_confidence(
        LCMFResult(status="accepted", angle_deg=17.0, component_std_deg=2.3, single_channel=False)
    )
    lone_component = server.vertical_confidence(
        LCMFResult(status="accepted", angle_deg=19.25, component_std_deg=0.0, single_channel=True)
    )
    recovered = server.vertical_confidence(
        LCMFResult(status="accepted", angle_deg=19.25, component_std_deg=9.1, single_channel=True)
    )
    assert lone_component == recovered
    assert lone_component < corroborated
    assert recovered < corroborated
    assert 0.0 < lone_component <= 1.0


def test_component_std_deg_none_is_defensive_only():
    """Defensive branch, not a reachable state: lcmf.py always computes
    ``component_std_deg`` as a float (``np.std`` of at least one value) any
    time ``single_channel`` is set, so ``None`` alongside ``single_channel``
    never occurs today. The branch is kept for a future estimator that might
    omit the field.
    """
    single = server.vertical_confidence(
        LCMFResult(status="accepted", angle_deg=19.25, component_std_deg=None, single_channel=True)
    )
    assert 0.0 < single <= 1.0


def _spread_scoring(score: float) -> float:
    """The ``component_std_deg`` a corroborated estimate needs to earn ``score``.

    Derived from the module's own constants rather than hardcoded, so the
    tests below pin *relationships* between the confidence constants without
    pinning any constant's value -- retuning the spread curve keeps them
    passing, while breaking the single-channel derate does not.
    """
    span = server.VERTICAL_SPREAD_ZERO_CONFIDENCE_DEG - server.VERTICAL_SPREAD_FULL_CONFIDENCE_DEG
    return server.VERTICAL_SPREAD_FULL_CONFIDENCE_DEG + (1.0 - score) * span


def test_single_channel_confidence_is_bounded_by_floor_and_equivalent_corroborated():
    """Pins both magnitudes in the single-channel path, which the structural
    tests above cannot see.

    ``test_single_channel_states_score_identically_and_rank_below_corroborated``
    asserts only *structure*, so it survives setting the fixed no-corroboration
    base to 0.0 or 1.0, and survives deleting the derate entirely -- all three
    keep both single-channel states equal and below a tightly-agreeing result.
    This sandwich closes that:

    - base -> 0.0 collapses the score onto ANGLE_CONFIDENCE_FLOOR, failing the
      lower bound: a measured single-channel angle must still beat the floor.
    - dropping SINGLE_CHANNEL_CONFIDENCE_FACTOR makes an uncorroborated
      estimate score *exactly* what a corroborated estimate carrying the same
      underlying score earns, failing the upper bound -- corroboration has to
      be worth something.
    - base -> 1.0 overshoots that same upper bound.

    These are bounds, not a lock on the value: any base in roughly (0.0, 0.625)
    still passes. That is deliberate. The honest requirement is the *ordering* --
    a measured single-channel angle beats the table fallback, and corroboration
    is worth strictly more than none -- so pinning the exact literal would make
    this test fail on a legitimate retune while catching nothing extra.
    """
    single = server.vertical_confidence(
        LCMFResult(status="accepted", angle_deg=19.25, component_std_deg=0.0, single_channel=True)
    )
    # A corroborated estimate whose spread earns the same pre-derate score the
    # single-channel path starts from, so the only difference is corroboration.
    equivalent_corroborated = server.vertical_confidence(
        LCMFResult(
            status="accepted",
            angle_deg=17.0,
            component_std_deg=_spread_scoring(0.5),
            single_channel=False,
        )
    )
    assert server.ANGLE_CONFIDENCE_FLOOR < single < equivalent_corroborated


def test_corroborated_outranks_single_channel_across_the_whole_admissible_spread():
    """Guards the coupling between ``CHANNEL_SPREAD_MAX_DEG`` and the spread
    curve, which is currently a 1.8x margin nobody wrote down.

    ``combine_channels`` admits two channels while their gap is within
    CHANNEL_SPREAD_MAX_DEG, so the widest ``np.std`` a corroborated result can
    carry is half that gap. Confidence falls with spread, so at some gap a
    corroborated estimate would score *below* an uncorroborated one -- which is
    the original defect re-pointed, since it would again reward absent evidence.
    Today that crossover sits near a 14.4 degree gap against an 8.0 degree
    gate. Raising the gate past the crossover silently re-creates the bug, so
    assert the invariant over the worst spread the gate actually admits rather
    than trusting the margin to stay wide.
    """
    widest_admissible_std = lcmf.CHANNEL_SPREAD_MAX_DEG / 2.0
    worst_corroborated = server.vertical_confidence(
        LCMFResult(
            status="accepted",
            angle_deg=17.0,
            component_std_deg=widest_admissible_std,
            single_channel=False,
        )
    )
    single = server.vertical_confidence(
        LCMFResult(status="accepted", angle_deg=19.25, component_std_deg=0.0, single_channel=True)
    )
    assert single < worst_corroborated, (
        "a corroborated estimate at the widest admissible channel gap "
        f"({lcmf.CHANNEL_SPREAD_MAX_DEG} deg) scored {worst_corroborated}, at or below the "
        f"uncorroborated {single}: CHANNEL_SPREAD_MAX_DEG now exceeds the point where "
        "channel agreement stops being worth more than no cross-check at all"
    )


def test_wide_component_spread_lowers_confidence():
    tight = server.vertical_confidence(
        LCMFResult(status="accepted", angle_deg=24.3, component_std_deg=2.3)
    )
    loose = server.vertical_confidence(
        LCMFResult(status="accepted", angle_deg=8.6, component_std_deg=9.1)
    )
    assert loose < tight


def test_horizontal_confidence_tracks_coherence():
    assert server.horizontal_confidence_from(0.95) > server.horizontal_confidence_from(0.71)
    assert server.horizontal_confidence_from(0.10) < 0.5
    assert server.horizontal_confidence_from(None) == 0.0

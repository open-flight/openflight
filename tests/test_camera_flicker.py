"""Lighting flicker normalization before camera club analysis."""

import numpy as np

from openflight.camera.club_delivery import normalize_flicker


def test_alternating_gain_is_removed_and_moving_object_kept():
    rng = np.random.default_rng(0)
    base = rng.integers(30, 60, size=(400, 640)).astype(float)
    gains = np.array([1.0, 0.55, 1.05, 0.6] * 10)  # 28<->54 style swing
    frames = np.stack([np.clip(base * g, 0, 255) for g in gains]).astype(np.uint8)
    frames[20, 200:220, 300:320] = 250  # a bright moving object in the upper scene
    out = normalize_flicker(frames)
    bottom = out[:, 300:, :].reshape(len(out), -1).mean(axis=1)
    assert bottom.std() / bottom.mean() < 0.02
    assert (
        out[20, 210, 310] > 3 * bottom[20]
    )  # the moving object still stands well clear of the scene


def test_steady_or_dark_frames_untouched():
    steady = np.full((25, 40, 60), 80, np.uint8)
    assert normalize_flicker(steady) is steady
    dark = np.zeros((25, 40, 60), np.uint8)
    assert normalize_flicker(dark) is dark


def test_screen_and_mat_flickering_out_of_step_are_both_steadied():
    # A projector screen (top rows) and the room lights
    # on the mat flicker on different schedules. Scaling whole frames by the
    # mat left the screen swinging 10.5%, and its jumps killed the club track.
    rng = np.random.default_rng(1)
    screen_gain = np.array([1.0, 1.3, 0.8, 1.2, 0.9, 1.25, 0.85, 1.1] * 4)
    mat_gain = np.array([1.0, 0.8, 1.2, 0.9, 1.1, 0.85, 1.15, 0.95] * 4)
    frames = np.empty((len(screen_gain), 200, 640), dtype=np.uint8)
    for i, (gs, gm) in enumerate(zip(screen_gain, mat_gain)):
        frames[i, :125] = np.clip(85 * gs + rng.normal(0, 2, (125, 640)), 0, 255)
        frames[i, 125:] = np.clip(40 * gm + rng.normal(0, 2, (75, 640)), 0, 255)
    out = normalize_flicker(frames).astype(float)
    screen = out[:, 20:100].mean(axis=(1, 2))
    mat = out[:, 150:195].mean(axis=(1, 2))
    assert screen.std() / screen.mean() < 0.02
    assert mat.std() / mat.mean() < 0.02

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Shot } from '../types/shot';
import { useShotStore } from './useShotStore';

const shot = {
  ball_speed_mph: 145,
  timestamp: '2026-08-12T12:00:00Z',
} as Shot;

describe('useShotStore processing lifecycle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    useShotStore.setState({
      latestShot: null,
      shots: [],
      isNewShot: false,
      shotProcessingPhase: null,
      shotProcessingShotTimestamp: null,
      shotVersion: 0,
    });
  });

  afterEach(() => {
    vi.clearAllTimers();
    vi.useRealTimers();
  });

  it('keeps current shot data while marking the next shot as processing', () => {
    useShotStore.setState({ latestShot: shot, shots: [shot] });

    useShotStore.getState().startShotProcessing('capturing');

    expect(useShotStore.getState().shotProcessingPhase).toBe('capturing');
    expect(useShotStore.getState().latestShot).toBe(shot);
    expect(useShotStore.getState().shots).toEqual([shot]);
  });

  it('clears processing when the completed shot arrives', () => {
    useShotStore.getState().startShotProcessing('capturing');
    useShotStore.getState().startShotProcessing('calculating');

    useShotStore.getState().addShot(shot);

    expect(useShotStore.getState().shotProcessingPhase).toBeNull();
  });

  it('clears a stale processing state after the watchdog timeout', () => {
    useShotStore.getState().startShotProcessing('capturing');

    vi.advanceTimersByTime(30_000);

    expect(useShotStore.getState().shotProcessingPhase).toBeNull();
  });

  it('replaces a provisional shot without replaying new-shot effects', () => {
    const enriched = {
      ...shot,
      launch_angle_vertical: 17.4,
      launch_angle_vertical_source: 'radar',
    } as Shot;
    useShotStore.setState({
      latestShot: shot,
      shots: [shot],
      shotProcessingPhase: 'iwr_dump',
      shotProcessingShotTimestamp: shot.timestamp,
      isNewShot: true,
      shotVersion: 1,
    });

    useShotStore.getState().updateShot(enriched);

    expect(useShotStore.getState().shots).toEqual([enriched]);
    expect(useShotStore.getState().latestShot).toBe(enriched);
    expect(useShotStore.getState().shotProcessingPhase).toBeNull();
    expect(useShotStore.getState().shotProcessingShotTimestamp).toBeNull();
    expect(useShotStore.getState().isNewShot).toBe(true);
    expect(useShotStore.getState().shotVersion).toBe(1);
  });

  it('does not clear a newer IWR dump indicator when an older shot update arrives', () => {
    const newerShot = { ...shot, timestamp: '2026-08-12T12:01:00Z' } as Shot;
    useShotStore.setState({
      latestShot: newerShot,
      shots: [shot, newerShot],
      shotProcessingPhase: 'iwr_dump',
      shotProcessingShotTimestamp: newerShot.timestamp,
    });

    useShotStore.getState().updateShot({ ...shot, launch_angle_vertical: 17.4 } as Shot);

    expect(useShotStore.getState().shotProcessingPhase).toBe('iwr_dump');
    expect(useShotStore.getState().shotProcessingShotTimestamp).toBe(newerShot.timestamp);
  });

  it('ignores enrichment for a shot that was already removed', () => {
    useShotStore.getState().updateShot(shot);

    expect(useShotStore.getState().shots).toEqual([]);
    expect(useShotStore.getState().latestShot).toBeNull();
  });
});

import { afterEach, describe, expect, it } from 'vitest';
import { setActiveLocale } from '../../i18n';
import type { Shot, SwingSpeedStats } from '../../types/shot';
import {
  buildLiveMetrics,
  LIVE_METRIC_COUNT,
  NO_VALUE,
  pinSelectedMetric,
  shouldEnableLiveBallWarning,
  SWING_METRIC_COUNT,
  type LiveMetric,
} from './liveMetrics';

const emptySwingStats: SwingSpeedStats = {
  count: 0,
  last_speed_mph: 0,
  best_speed_mph: 0,
  avg_speed_mph: 0,
};

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 92,
    club_speed_mph: 68,
    smash_factor: 1.35,
    estimated_carry_yards: 210,
    carry_range: [205, 215],
    club: 'driver',
    timestamp: '2026-08-19T10:00:00Z',
    peak_magnitude: 100,
    launch_angle_vertical: 13.4,
    launch_angle_horizontal: -1.2,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: 2.1,
    club_path_deg: -0.6,
    spin_axis_deg: 3.4,
    spin_rpm: 2650,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: 214,
    ...overrides,
  };
}

function byId(metrics: LiveMetric[], id: string): LiveMetric {
  const metric = metrics.find((m) => m.id === id);
  if (!metric) throw new Error(`no metric ${id}`);
  return metric;
}

describe('buildLiveMetrics', () => {
  afterEach(() => {
    setActiveLocale('en');
  });

  it('returns a fixed ten metrics for a ball-strike shot, including club AoA', () => {
    const metrics = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);

    expect(metrics).toHaveLength(LIVE_METRIC_COUNT);
    expect(new Set(metrics.map((m) => m.id)).size).toBe(LIVE_METRIC_COUNT);
    expect(metrics.map((m) => m.id)).toEqual([
      'ball_speed',
      'carry',
      'club_speed',
      'smash',
      'launch_v',
      'launch_h',
      'spin',
      'spin_axis',
      'club_path',
      'club_aoa',
    ]);
    expect(byId(metrics, 'club_aoa')).toMatchObject({ value: '+2.1', unit: '°', label: 'Club AoA' });
  });

  it('keeps the same ten metrics in the same order when values are missing', () => {
    const full = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);
    const sparse = buildLiveMetrics(
      makeShot({
        club_speed_mph: null,
        smash_factor: null,
        launch_angle_vertical: null,
        launch_angle_horizontal: null,
        launch_angle_confidence: null,
        club_path_deg: null,
        spin_axis_deg: null,
        spin_rpm: null,
        spin_quality: null,
        spin_source: null,
        carry_spin_adjusted: null,
      }),
      'imperial',
      emptySwingStats
    );

    // The grid must not reflow between shots.
    expect(sparse.map((m) => m.id)).toEqual(full.map((m) => m.id));
    expect(byId(sparse, 'club_speed').value).toBe(NO_VALUE);
    expect(byId(sparse, 'spin').value).toBe(NO_VALUE);
    expect(byId(sparse, 'spin').confidence).toBeNull();
    // No stale unit hanging off a placeholder.
    expect(byId(sparse, 'club_speed').unit).toBeUndefined();
  });

  it('formats ball speed and carry in the selected unit system', () => {
    const imperial = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);
    const metric = buildLiveMetrics(makeShot(), 'metric', emptySwingStats);

    expect(byId(imperial, 'ball_speed')).toMatchObject({ value: '92.0', unit: 'mph' });
    expect(byId(metric, 'ball_speed')).toMatchObject({ value: '148.1', unit: 'km/h' });
    expect(byId(imperial, 'carry')).toMatchObject({ value: '214', unit: 'yds' });
    expect(byId(metric, 'carry')).toMatchObject({ value: '196', unit: 'm' });
  });

  it('prefers the spin-adjusted carry and marks model carry as estimated', () => {
    const adjusted = byId(buildLiveMetrics(makeShot(), 'imperial', emptySwingStats), 'carry');
    expect(adjusted.value).toBe('214');
    expect(adjusted.subtext).toBe('Spin-adjusted');
    expect(adjusted.estimated).toBeUndefined();

    const estimated = byId(
      buildLiveMetrics(makeShot({ carry_spin_adjusted: null }), 'imperial', emptySwingStats),
      'carry'
    );
    expect(estimated.value).toBe('210');
    expect(estimated.subtext).toBeUndefined();
    expect(estimated.estimated).toBe(true);
  });

  it('signs the directional angles and labels the shot shape', () => {
    const metrics = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);

    expect(byId(metrics, 'launch_h').value).toBe('-1.2');
    expect(byId(metrics, 'club_path').value).toBe('-0.6');
    expect(byId(metrics, 'club_aoa').value).toBe('+2.1');

    const negativeAoa = buildLiveMetrics(makeShot({ club_angle_deg: -3.2 }), 'imperial', emptySwingStats);
    expect(byId(negativeAoa, 'club_aoa').value).toBe('-3.2');
    expect(byId(metrics, 'spin_axis')).toMatchObject({ value: '+3.4', subtext: 'Fade' });

    const draw = buildLiveMetrics(makeShot({ spin_axis_deg: -4 }), 'imperial', emptySwingStats);
    expect(byId(draw, 'spin_axis').subtext).toBe('Draw');

    const straight = buildLiveMetrics(makeShot({ spin_axis_deg: 0.5 }), 'imperial', emptySwingStats);
    expect(byId(straight, 'spin_axis').subtext).toBe('Straight');
  });

  it('maps launch-angle confidence onto the dot scale', () => {
    const high = buildLiveMetrics(makeShot({ launch_angle_confidence: 0.9 }), 'imperial', emptySwingStats);
    const medium = buildLiveMetrics(makeShot({ launch_angle_confidence: 0.5 }), 'imperial', emptySwingStats);
    const low = buildLiveMetrics(makeShot({ launch_angle_confidence: 0.1 }), 'imperial', emptySwingStats);

    expect(byId(high, 'launch_v').confidence).toBe('high');
    expect(byId(medium, 'launch_v').confidence).toBe('medium');
    expect(byId(low, 'launch_v').confidence).toBe('low');
  });

  it('preserves camera-fused club delivery values and confidence', () => {
    const metrics = buildLiveMetrics(
      makeShot({
        club_angle_deg: null,
        club_path_deg: null,
        experimental_fused_attack_angle_deg: -4.2,
        experimental_fused_attack_angle_confidence: 'medium',
        experimental_fused_club_path_deg: 3.1,
        experimental_fused_club_path_confidence: 'high',
        experimental_fused_status: 'approach_mixed',
      }),
      'imperial',
      emptySwingStats
    );

    expect(byId(metrics, 'club_aoa')).toMatchObject({
      value: '-4.2',
      unit: '°',
      subtext: 'Fused',
      confidence: 'medium',
      experimental: true,
    });
    expect(byId(metrics, 'club_path')).toMatchObject({
      value: '+3.1',
      unit: '°',
      subtext: 'Fused',
      confidence: 'high',
      experimental: true,
    });
    expect(byId(metrics, 'club_aoa').confidenceLabel).toBeUndefined();
    expect(byId(metrics, 'club_path').confidenceLabel).toBeUndefined();
  });

  it('keeps camera-fusion rejection status but hides superseded radar candidates', () => {
    const metrics = buildLiveMetrics(
      makeShot({
        club_angle_deg: null,
        club_path_deg: null,
        experimental_attack_angle_deg: -32.2,
        experimental_club_path_deg: 130.9,
        experimental_fused_status: 'rejected_no_impact',
      }),
      'imperial',
      emptySwingStats
    );

    expect(byId(metrics, 'club_aoa')).toMatchObject({
      value: NO_VALUE,
      subtext: 'rejected: no impact',
      confidence: null,
      experimental: true,
    });
    expect(byId(metrics, 'club_path')).toMatchObject({
      value: NO_VALUE,
      subtext: 'rejected: no impact',
      confidence: null,
      experimental: true,
    });
  });

  it('labels camera-assisted horizontal launch', () => {
    const metrics = buildLiveMetrics(
      makeShot({ launch_angle_horizontal_source: 'camera_assisted_experimental' }),
      'imperial',
      emptySwingStats
    );

    expect(byId(metrics, 'launch_h')).toMatchObject({
      subtext: 'Camera',
      experimental: true,
    });
  });

  it('translates camera provenance in the active locale', () => {
    setActiveLocale('es');
    const metrics = buildLiveMetrics(
      makeShot({
        club_angle_deg: null,
        club_path_deg: null,
        launch_angle_horizontal_source: 'camera_assisted_experimental',
        experimental_fused_attack_angle_deg: -4.2,
        experimental_fused_club_path_deg: 3.1,
        experimental_fused_status: 'approach_mixed',
      }),
      'imperial',
      emptySwingStats
    );

    expect(byId(metrics, 'launch_h').subtext).toBe('Cámara');
    expect(byId(metrics, 'club_path').subtext).toBe('Fusión');
    expect(byId(metrics, 'club_aoa').subtext).toBe('Fusión');
  });

  it('marks estimated launch and spin with a flag, not provenance subtext', () => {
    const measured = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);
    expect(byId(measured, 'launch_v').subtext).toBeUndefined();
    expect(byId(measured, 'launch_v').estimated).toBeUndefined();
    expect(byId(measured, 'launch_h').estimated).toBeUndefined();
    expect(byId(measured, 'spin').subtext).toBeUndefined();
    expect(byId(measured, 'spin').estimated).toBeUndefined();

    const estimated = buildLiveMetrics(
      makeShot({ angle_source: 'estimated', spin_source: 'calculated' }),
      'imperial',
      emptySwingStats
    );
    expect(byId(estimated, 'launch_v').estimated).toBe(true);
    expect(byId(estimated, 'launch_h').estimated).toBe(true);
    expect(byId(estimated, 'spin').estimated).toBe(true);
    expect(byId(estimated, 'launch_v').subtext).toBeUndefined();
    expect(byId(estimated, 'spin').subtext).toBeUndefined();
  });

  it('returns the swing-speed set, with ids that never collide with ball-strike ids', () => {
    const stats: SwingSpeedStats = { count: 12, last_speed_mph: 101, best_speed_mph: 108, avg_speed_mph: 99 };
    const swing = buildLiveMetrics(
      makeShot({ mode: 'swing-speed', training_implement_label: 'Stack 100g', swing_speed_reading_count: 7 }),
      'imperial',
      stats
    );
    const ballStrike = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);

    expect(swing).toHaveLength(SWING_METRIC_COUNT);
    expect(byId(swing, 'swing_best').value).toBe('108.0');
    expect(byId(swing, 'swing_implement').value).toBe('Stack 100g');

    const shared = swing.map((m) => m.id).filter((id) => ballStrike.some((m) => m.id === id));
    expect(shared).toEqual([]);
  });
});

describe('pinSelectedMetric', () => {
  const metrics = buildLiveMetrics(makeShot(), 'imperial', emptySwingStats);

  it('moves the selected metric to the front and keeps the rest in order', () => {
    const pinned = pinSelectedMetric(metrics, 'spin');

    expect(pinned.map((m) => m.id)).toEqual([
      'spin',
      'ball_speed',
      'carry',
      'club_speed',
      'smash',
      'launch_v',
      'launch_h',
      'spin_axis',
      'club_path',
      'club_aoa',
    ]);
  });

  it('falls back to the first metric when the stored id is absent', () => {
    expect(pinSelectedMetric(metrics, 'swing_best')[0]?.id).toBe('ball_speed');
    expect(pinSelectedMetric(metrics, null)[0]?.id).toBe('ball_speed');
    expect(pinSelectedMetric(metrics, 'nonsense')).toHaveLength(LIVE_METRIC_COUNT);
  });

  it('handles an empty metric list', () => {
    expect(pinSelectedMetric([], 'ball_speed')).toEqual([]);
  });
});

describe('shouldEnableLiveBallWarning', () => {
  const cameraOn = { available: true, enabled: true };

  it('is true only on the Live tab', () => {
    expect(shouldEnableLiveBallWarning('live', cameraOn)).toBe(true);
    expect(shouldEnableLiveBallWarning('stats', cameraOn)).toBe(false);
    expect(shouldEnableLiveBallWarning('shots', cameraOn)).toBe(false);
    expect(shouldEnableLiveBallWarning('camera', cameraOn)).toBe(false);
    expect(shouldEnableLiveBallWarning('debug', cameraOn)).toBe(false);
  });

  it('is false when the camera is unavailable or disabled', () => {
    expect(shouldEnableLiveBallWarning('live', { available: false, enabled: true })).toBe(false);
    expect(shouldEnableLiveBallWarning('live', { available: true, enabled: false })).toBe(false);
  });
});

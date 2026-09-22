import { describe, expect, it } from 'vitest';
import type { Shot } from '../types/shot';
import { remainingShotsAfterClear } from './sessionClear';

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 90,
    club_speed_mph: 67,
    smash_factor: 1.34,
    estimated_carry_yards: 200,
    carry_range: [195, 205],
    club: 'driver',
    timestamp: 'a',
    peak_magnitude: 100,
    launch_angle_vertical: 13,
    launch_angle_horizontal: 0,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: null,
    club_path_deg: null,
    spin_axis_deg: null,
    spin_rpm: 2600,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: null,
    ...overrides,
  };
}

describe('remainingShotsAfterClear', () => {
  const james = makeShot({ profile_id: 'aaa', timestamp: 'j' });
  const alex = makeShot({ profile_id: 'bbb', timestamp: 'a' });

  it('prefers the remaining shot list from the server', () => {
    expect(remainingShotsAfterClear([james, alex], { profile_id: 'aaa', shots: [alex] })).toEqual([alex]);
  });

  it('clears everything when given a legacy empty payload', () => {
    expect(remainingShotsAfterClear([james, alex])).toEqual([]);
    expect(remainingShotsAfterClear([james, alex], null)).toEqual([]);
  });

  it('falls back to dropping one profile by id', () => {
    const current = [{ profile_id: 'aaa' } as Shot, { profile_id: 'bbb' } as Shot];

    expect(remainingShotsAfterClear(current, { profile_id: 'aaa' })).toEqual([current[1]]);
  });
});

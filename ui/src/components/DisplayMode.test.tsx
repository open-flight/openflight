import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { CameraCaptureSettings } from '../stores/useCameraStore';
import type { Shot } from '../types/shot';
import { DisplayMode } from './DisplayMode';

const captureSettings: CameraCaptureSettings = {
  available: true,
  enabled: true,
  running: true,
};

const shot: Shot = {
  ball_speed_mph: 151.2,
  club_speed_mph: 101.1,
  smash_factor: 1.5,
  estimated_carry_yards: 254,
  carry_range: [244, 264],
  club: 'driver',
  timestamp: '2026-05-18T12:00:00Z',
  peak_magnitude: 42,
  launch_angle_vertical: 13.4,
  launch_angle_horizontal: -1.2,
  launch_angle_confidence: 0.82,
  angle_source: 'radar',
  club_angle_deg: 1.1,
  club_path_deg: 2.5,
  spin_axis_deg: -3.1,
  spin_rpm: 2450,
  spin_confidence: 0.8,
  spin_quality: 'high',
  spin_source: 'calculated',
  carry_spin_adjusted: 261,
};

describe('DisplayMode', () => {
  it('renders latest shot metrics and recent shot strip', () => {
    const html = renderToString(
      <DisplayMode connected captureSettings={captureSettings} latestShot={shot} shots={[shot]} />
    );

    expect(html).toContain('OpenFlight Display');
    expect(html).toContain('151.2');
    expect(html).toContain('261');
    expect(html).toContain('Socket connected');
    expect(html).toContain('display-shot-chip__number');
    expect(html).toContain('metric-card--emphasis');
    expect(html).not.toContain('display-metric');
  });

  it('does not request a preview when camera capture is unavailable', () => {
    const html = renderToString(
      <DisplayMode connected captureSettings={{ available: false }} latestShot={shot} shots={[shot]} />
    );

    expect(html).not.toContain('/api/camera/preview.jpg');
    expect(html).toContain('Camera unavailable');
  });

  it('shows rejection details for status-only experimental club metrics', () => {
    const rejectedShot: Shot = {
      ...shot,
      club_angle_deg: null,
      club_path_deg: null,
      experimental_attack_angle_status: 'rejected_no_club_track',
      experimental_club_path_status: 'rejected_no_pre_impact_frames',
    };

    const html = renderToString(
      <DisplayMode connected captureSettings={captureSettings} latestShot={rejectedShot} shots={[rejectedShot]} />
    );

    expect(html).toContain('experimental · rejected: no club track');
    expect(html).toContain('experimental · rejected: no pre impact frames');
  });
});

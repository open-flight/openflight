import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { CameraStatus } from '../stores/useCameraStore';
import type { Shot } from '../types/shot';
import { DisplayMode } from './DisplayMode';

const cameraStatus: CameraStatus = {
  available: true,
  enabled: true,
  streaming: true,
  ball_detected: false,
  ball_confidence: 0,
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
    const html = renderToString(<DisplayMode connected cameraStatus={cameraStatus} latestShot={shot} shots={[shot]} />);

    expect(html).toContain('OpenFlight Display');
    expect(html).toContain('151.2');
    expect(html).toContain('261');
    expect(html).toContain('Socket connected');
    expect(html).toContain('display-shot-chip__number');
    expect(html).toContain('metric-card--emphasis');
    expect(html).not.toContain('display-metric');
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
      <DisplayMode connected cameraStatus={cameraStatus} latestShot={rejectedShot} shots={[rejectedShot]} />
    );

    expect(html).toContain('metric-card__experimental');
    expect(html).toContain('rejected: no club track');
    expect(html).toContain('rejected: no pre impact frames');
    expect(html).not.toContain('experimental ·');
  });

  it('marks camera-fused and camera-assisted metrics with short copy and an icon', () => {
    const fusedShot: Shot = {
      ...shot,
      club_angle_deg: null,
      club_path_deg: null,
      launch_angle_horizontal_source: 'camera_assisted_experimental',
      experimental_fused_attack_angle_deg: -4.2,
      experimental_fused_club_path_deg: 3.1,
      experimental_fused_status: 'approach_mixed',
    };

    const html = renderToString(
      <DisplayMode connected cameraStatus={cameraStatus} latestShot={fusedShot} shots={[fusedShot]} />
    );

    expect(html).toContain('metric-card__experimental');
    expect(html).toMatch(/metric-card__subtext[^>]*>Fused</);
    expect(html).toMatch(/metric-card__subtext[^>]*>Camera</);
    expect(html).not.toContain('camera fused');
    expect(html).not.toContain('camera assisted');
  });
});

/**
 * Club-keyed gaussian shot generation, ported from Python MockLaunchMonitor.
 */

import { randomUUID } from 'node:crypto';
import type { Shot, SpinQuality } from '../src/types/shot.js';

/** [avg_ball_speed, std_dev, smash_factor] */
const CLUB_BALL_SPEEDS: Record<string, [number, number, number]> = {
  driver: [143, 12, 1.45],
  '3-wood': [135, 10, 1.42],
  '5-wood': [128, 10, 1.4],
  '7-wood': [122, 9, 1.4],
  '3-hybrid': [123, 9, 1.39],
  '5-hybrid': [118, 9, 1.37],
  '7-hybrid': [112, 8, 1.35],
  '9-hybrid': [106, 8, 1.33],
  '2-iron': [120, 9, 1.35],
  '3-iron': [118, 9, 1.35],
  '4-iron': [114, 8, 1.33],
  '5-iron': [110, 8, 1.31],
  '6-iron': [105, 7, 1.29],
  '7-iron': [100, 7, 1.27],
  '8-iron': [94, 6, 1.25],
  '9-iron': [88, 6, 1.23],
  pw: [82, 5, 1.21],
  gw: [76, 5, 1.2],
  sw: [73, 5, 1.19],
  lw: [70, 5, 1.18],
  unknown: [120, 15, 1.35],
};

/** [avg_rpm, std_dev] */
const CLUB_SPIN: Record<string, [number, number]> = {
  driver: [2700, 400],
  '3-wood': [3200, 400],
  '5-wood': [3700, 400],
  '7-wood': [4200, 500],
  '3-hybrid': [3800, 400],
  '5-hybrid': [4200, 500],
  '7-hybrid': [4600, 500],
  '9-hybrid': [5000, 500],
  '2-iron': [3800, 400],
  '3-iron': [4100, 400],
  '4-iron': [4500, 500],
  '5-iron': [5000, 500],
  '6-iron': [5500, 600],
  '7-iron': [6000, 600],
  '8-iron': [7000, 700],
  '9-iron': [7800, 800],
  pw: [8500, 800],
  gw: [9200, 900],
  sw: [9800, 1000],
  lw: [10200, 1000],
  unknown: [5000, 800],
};

/** [avg_launch_deg, std_dev] */
const CLUB_LAUNCH: Record<string, [number, number]> = {
  driver: [11.0, 2.0],
  '3-wood': [12.5, 2.0],
  '5-wood': [14.0, 2.0],
  '7-wood': [15.5, 2.0],
  '3-hybrid': [13.5, 2.0],
  '5-hybrid': [15.0, 2.0],
  '7-hybrid': [16.5, 2.0],
  '9-hybrid': [18.0, 2.5],
  '2-iron': [13.0, 2.0],
  '3-iron': [14.5, 2.0],
  '4-iron': [16.0, 2.0],
  '5-iron': [17.5, 2.0],
  '6-iron': [19.0, 2.5],
  '7-iron': [20.5, 2.5],
  '8-iron': [23.0, 3.0],
  '9-iron': [25.5, 3.0],
  pw: [28.0, 3.0],
  gw: [30.0, 3.5],
  sw: [32.0, 4.0],
  lw: [35.0, 4.0],
  unknown: [18.0, 3.0],
};

const SPIN_CONFIDENCES = [0.3, 0.6, 0.7, 0.9] as const;

function gauss(mean: number, stdDev: number): number {
  // Box–Muller
  const u1 = Math.max(Number.EPSILON, Math.random());
  const u2 = Math.random();
  const z = Math.sqrt(-2 * Math.log(u1)) * Math.cos(2 * Math.PI * u2);
  return mean + z * stdDev;
}

function uniform(min: number, max: number): number {
  return min + Math.random() * (max - min);
}

function pick<T>(items: readonly T[]): T {
  return items[Math.floor(Math.random() * items.length)]!;
}

function spinQuality(confidence: number): SpinQuality {
  if (confidence >= 0.7) return 'high';
  if (confidence >= 0.4) return 'medium';
  return 'low';
}

/** Simple TrackMan-ish carry estimate (yards) from ball speed. */
export function estimateCarryYards(ballSpeedMph: number): number {
  return Math.round(ballSpeedMph * 1.45);
}

export interface GenerateShotOptions {
  club: string;
  profileId: string;
  profileName: string;
  ballSpeed?: number;
}

export function generateShot(options: GenerateShotOptions): Shot {
  const club = options.club in CLUB_BALL_SPEEDS ? options.club : 'unknown';
  const [avgSpeed, speedStd, smash] = CLUB_BALL_SPEEDS[club] ?? CLUB_BALL_SPEEDS.unknown!;
  const [avgSpin, spinStd] = CLUB_SPIN[club] ?? CLUB_SPIN.unknown!;
  const [avgLaunch, launchStd] = CLUB_LAUNCH[club] ?? CLUB_LAUNCH.unknown!;

  const ballSpeed = options.ballSpeed ?? Math.max(50, Math.min(200, gauss(avgSpeed, speedStd)));
  const smashFactor = smash + uniform(-0.03, 0.03);
  const clubSpeed = ballSpeed / smashFactor;
  const spinRpm = Math.max(1000, gauss(avgSpin, spinStd));
  const spinConfidence = pick(SPIN_CONFIDENCES);
  const launchV = Math.max(5.0, gauss(avgLaunch, launchStd));
  const launchH = gauss(0, 2.0);
  const launchConfidence = Math.round(uniform(0.5, 0.95) * 100) / 100;
  const clubAoa = Math.round(gauss(-4.0, 2.5) * 10) / 10;
  const clubPath = Math.round(uniform(-5.0, 5.0) * 10) / 10;
  const spinAxis = Math.round((launchH - uniform(-5.0, 5.0)) * 10) / 10;
  const carry = estimateCarryYards(ballSpeed);
  const smashRounded = Math.round((ballSpeed / clubSpeed) * 100) / 100;

  return {
    mode: 'mock',
    ball_speed_mph: Math.round(ballSpeed * 10) / 10,
    club_speed_mph: Math.round(clubSpeed * 10) / 10,
    smash_factor: smashRounded,
    estimated_carry_yards: carry,
    carry_range: [Math.round(carry * 0.95), Math.round(carry * 1.05)],
    club,
    profile_id: options.profileId,
    profile_name: options.profileName,
    timestamp: new Date().toISOString(),
    peak_magnitude: null,
    launch_angle_vertical: Math.round(launchV * 10) / 10,
    launch_angle_horizontal: Math.round(launchH * 10) / 10,
    launch_angle_confidence: launchConfidence,
    angle_source: 'estimated',
    club_angle_deg: clubAoa,
    club_path_deg: clubPath,
    spin_axis_deg: spinAxis,
    spin_rpm: Math.round(spinRpm),
    spin_confidence: Math.round(spinConfidence * 100) / 100,
    spin_quality: spinQuality(spinConfidence),
    spin_source: 'calculated',
    carry_spin_adjusted: carry,
    camera_replay: {
      id: `mock-${randomUUID()}`,
      frame_count: 99,
      trigger_frame: 73,
      playback_fps: 60,
      duration_seconds: 1.65,
      display_mirror_horizontal: true,
    },
  };
}

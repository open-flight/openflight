/**
 * In-memory mock session: shots, club/profile, and stats recompute.
 */

import type { SessionStats, Shot, TriggerStatus } from '../src/types/shot.js';
import type { RadarConfig } from '../src/types/socket.js';
import type { Profile } from '../src/types/profile.js';
import { bagClubs, clubShortLabel, findCustomClub, type CustomClub } from '../src/data/bag.js';
import { getClubName } from '../src/data/clubs.js';
import { generateShot } from './shotGenerator.js';

// Nominal lofts a blank custom-club loft inherits; mirrors CLUB_PHYSICS in
// src/openflight/clubs/physics.py.
const NOMINAL_LOFTS: Record<string, number> = {
  driver: 10.5,
  '3-wood': 15,
  '5-wood': 18,
  '7-wood': 21,
  '3-hybrid': 19,
  '5-hybrid': 22,
  '7-hybrid': 25,
  '9-hybrid': 28,
  '2-iron': 18,
  '3-iron': 21,
  '4-iron': 24,
  '5-iron': 27,
  '6-iron': 30.5,
  '7-iron': 34,
  '8-iron': 38,
  '9-iron': 42,
  pw: 46,
  gw: 50,
  sw: 54,
  lw: 58,
};

function mean(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function stdDev(values: number[]): number {
  if (values.length < 2) return 0;
  const avg = mean(values);
  const variance = mean(values.map((v) => (v - avg) ** 2));
  return Math.sqrt(variance);
}

export function computeSessionStats(shots: Shot[]): SessionStats {
  if (shots.length === 0) {
    return {
      shot_count: 0,
      avg_ball_speed: 0,
      max_ball_speed: 0,
      min_ball_speed: 0,
      avg_club_speed: null,
      avg_smash_factor: null,
      avg_carry_est: 0,
    };
  }

  const ballSpeeds = shots.map((s) => s.ball_speed_mph);
  const clubSpeeds = shots.map((s) => s.club_speed_mph).filter((v): v is number => v != null);
  const smashFactors = shots.map((s) => s.smash_factor).filter((v): v is number => v != null);
  const spinRpms = shots.map((s) => s.spin_rpm).filter((v): v is number => v != null);

  return {
    shot_count: shots.length,
    avg_ball_speed: mean(ballSpeeds),
    max_ball_speed: Math.max(...ballSpeeds),
    min_ball_speed: Math.min(...ballSpeeds),
    std_dev: stdDev(ballSpeeds),
    avg_club_speed: clubSpeeds.length ? mean(clubSpeeds) : null,
    avg_smash_factor: smashFactors.length ? mean(smashFactors) : null,
    avg_carry_est: mean(shots.map((s) => s.estimated_carry_yards)),
    avg_spin_rpm: spinRpms.length ? mean(spinRpms) : null,
    spin_detection_rate: shots.length ? spinRpms.length / shots.length : 0,
    mode: 'rolling-buffer',
  };
}

export class MockSession {
  shots: Shot[] = [];
  club = 'driver';
  /** The selected custom club, or "" (mirrors the server's active_custom_club_id). */
  customClubId = '';
  /** Every bag's custom clubs, as the server's clubs.json holds them. */
  customClubs: CustomClub[] = [];
  private nextClubNumber = 1;
  profiles: Profile[] = [{ id: 'mock-profile-1', name: 'Profile 1', created_at: '2026-01-01T00:00:00Z', settings: {} }];
  activeProfileId = 'mock-profile-1';
  private nextProfileNumber = 2;
  trainingImplement = 'driver';
  debugMode = false;
  radarConfig: RadarConfig = {
    min_speed: 10,
    max_speed: 220,
    min_magnitude: 0,
    transmit_power: 0,
  };
  triggersTotal = 0;
  triggersAccepted = 0;
  triggersRejected = 0;

  getStats(): SessionStats {
    return computeSessionStats(this.shots);
  }

  get activeProfile(): Profile {
    return this.profiles.find((profile) => profile.id === this.activeProfileId) ?? this.profiles[0]!;
  }

  snapshot() {
    return { profiles: this.profiles, active_profile_id: this.activeProfile.id };
  }

  addProfile(rawName: unknown): void {
    const name = String(rawName ?? '')
      .trim()
      .slice(0, 40);
    if (!name || this.profiles.length >= 12) return;
    const profile: Profile = {
      id: `mock-profile-${this.nextProfileNumber++}`,
      name,
      created_at: new Date().toISOString(),
      settings: {},
    };
    this.profiles.push(profile);
    this.activeProfileId = profile.id;
  }

  renameProfile(profileId: unknown, rawName: unknown): void {
    const name = String(rawName ?? '')
      .trim()
      .slice(0, 40);
    const profile = this.profiles.find((entry) => entry.id === profileId);
    if (!name || !profile) return;
    profile.name = name;
  }

  removeProfile(profileId: unknown): void {
    // Same refusals as the real store: never the active one, never the last,
    // never one that still has session rows (those would be orphaned).
    if (profileId === this.activeProfileId || this.profiles.length <= 1) return;
    if (this.shots.some((shot) => shot.profile_id === profileId)) return;
    this.profiles = this.profiles.filter((entry) => entry.id !== profileId);
  }

  setActiveProfile(profileId: unknown): void {
    if (this.profiles.some((entry) => entry.id === profileId)) {
      this.activeProfileId = String(profileId);
    }
  }

  clearProfile(profileId: string): void {
    this.shots = this.shots.filter((shot) => shot.profile_id !== profileId);
  }

  sessionStatePayload(includeMeta = true) {
    const base = {
      stats: this.getStats(),
      shots: this.shots,
      club: this.club,
      custom_club_id: this.customClubId,
    };
    if (!includeMeta) {
      return base;
    }
    return {
      ...base,
      mock_mode: true,
      debug_mode: this.debugMode,
    };
  }

  triggerStatus(): TriggerStatus {
    return {
      mode: 'mock',
      trigger_type: 'simulate',
      radar_connected: false,
      radar_port: null,
      triggers_total: this.triggersTotal,
      triggers_accepted: this.triggersAccepted,
      triggers_rejected: this.triggersRejected,
    };
  }

  get activeBag(): CustomClub[] {
    return bagClubs(this.customClubs, this.activeProfile.id);
  }

  /** The Socket.IO `clubs` payload. */
  clubsPayload() {
    return { version: 1, clubs: this.customClubs, lofts: NOMINAL_LOFTS };
  }

  /**
   * Same rules as the server's apply_club_selection, minus validation: a custom
   * club id implies its type; a type-only selection keeps the selected custom
   * club of that type, else lands on the bag's only one.
   */
  setClub(club: string, customClubId = ''): { club: string; custom_club_id: string } {
    const bag = this.activeBag;
    const explicit = findCustomClub(bag, customClubId);
    this.club = explicit?.club || club || 'driver';
    const current = findCustomClub(bag, this.customClubId);
    const sameType = bag.filter((entry) => entry.club === this.club);
    const resolved = explicit ?? (current?.club === this.club ? current : sameType.length === 1 ? sameType[0] : null);
    this.customClubId = resolved?.id ?? '';
    return { club: this.club, custom_club_id: this.customClubId };
  }

  /**
   * Add a club (no `id`) to `bag` or the active bag, or update one, keeping its
   * id and bag. Blank lofts inherit the type's, as on the server; nothing else
   * is validated here.
   */
  saveCustomClub(data: Partial<CustomClub> & { loft_deg?: number | null }): void {
    const existing = findCustomClub(this.customClubs, data.id ?? '');
    const club = data.club ?? existing?.club ?? 'sw';
    const saved: CustomClub = {
      id: existing?.id ?? `mock-club-${this.nextClubNumber++}`,
      bag: existing?.bag ?? data.bag ?? this.activeProfile.id,
      club,
      name: getClubName(club),
      label: clubShortLabel(club),
      loft_deg: data.loft_deg ?? NOMINAL_LOFTS[club] ?? 0,
      brand: data.brand ?? '',
      model: data.model ?? '',
      bounce_deg: data.bounce_deg ?? null,
      lie_deg: data.lie_deg ?? null,
      shaft: data.shaft ?? '',
      hand: data.hand ?? null,
      notes: data.notes ?? '',
    };
    this.customClubs = existing
      ? this.customClubs.map((entry) => (entry.id === saved.id ? saved : entry))
      : [...this.customClubs, saved];
  }

  removeCustomClub(clubId: unknown): void {
    this.customClubs = this.customClubs.filter((entry) => entry.id !== clubId);
  }

  /** Drop a selection that is no longer in the active bag. True when it changed. */
  reconcileCustomClub(): boolean {
    const before = this.customClubId;
    this.setClub(this.club);
    return before !== this.customClubId;
  }

  setTrainingImplement(implement: string): string {
    this.trainingImplement = implement || 'driver';
    return this.trainingImplement;
  }

  simulateShot(): { shot: Shot; stats: SessionStats } {
    const shot = generateShot({
      club: this.club,
      profileId: this.activeProfile.id,
      profileName: this.activeProfile.name,
    });
    shot.custom_club = findCustomClub(this.activeBag, this.customClubId);
    shot.custom_club_id = shot.custom_club?.id ?? '';
    this.shots.push(shot);
    this.triggersTotal += 1;
    this.triggersAccepted += 1;
    return { shot, stats: this.getStats() };
  }

  clear(): void {
    this.shots = [];
  }

  deleteShot(timestamp: string | undefined): boolean {
    if (!timestamp) return false;
    const before = this.shots.length;
    this.shots = this.shots.filter((s) => s.timestamp !== timestamp);
    return this.shots.length < before;
  }

  toggleDebug(): boolean {
    this.debugMode = !this.debugMode;
    return this.debugMode;
  }

  updateRadarConfig(partial: Partial<RadarConfig>): RadarConfig {
    this.radarConfig = { ...this.radarConfig, ...partial };
    return this.radarConfig;
  }
}

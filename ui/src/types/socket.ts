export interface DebugReading {
  speed: number;
  direction: 'inbound' | 'outbound' | 'unknown';
  magnitude: number | null;
  timestamp: string;
}

export type SimState = 'connected' | 'connecting' | 'reconnecting' | 'disabled' | 'stopped' | 'error';

export interface SimStatus {
  target: string;
  state: SimState;
  host?: string;
  port?: number;
  message?: string;
  attempt?: number;
  next_retry_in_s?: number;
}

export interface SimShotInfo {
  target: string;
  shot_number: number;
  fields: string[];
  values: Record<string, number | null>;
  provenance: Record<string, 'measured' | 'estimated'>;
}

export interface SwingSpeedEvent {
  peak_speed_mph: number;
  timestamp: string;
  duration_ms: number;
  reading_count: number;
  trigger_speed_mph: number;
  peak_magnitude: number | null;
  profile_id?: string;
  profile_name?: string;
  unit: string;
  mode: 'swing-speed';
}

export interface RadarConfig {
  min_speed: number;
  max_speed: number;
  min_magnitude: number;
  transmit_power: number;
}

export interface DebugShotLog {
  type: 'shot';
  timestamp: string;
  radar: {
    ball_speed_mph: number;
    club_speed_mph: number | null;
    smash_factor: number | null;
    peak_magnitude: number;
  };
  camera: {
    launch_angle_vertical: number;
    launch_angle_horizontal: number;
    launch_angle_confidence: number;
    positions_tracked: number;
    launch_detected: boolean;
  } | null;
  club: string;
}

import type { Shot, SpinQuality, SwingSpeedStats } from '../../types/shot';
import { getSwingSpeedMph, isSwingSpeedShot } from '../../types/shot';
import type { UnitSystem } from '../../utils/units';
import { formatDistance, formatSpeed, getDistanceUnit, getSpeedUnit } from '../../utils/units';
import { getHtmlLang, t } from '../../i18n';
import type { PanelView } from './views';

/** Placeholder for a metric the current shot has no value for. */
export const NO_VALUE = '—';

export interface LiveMetric {
  /** Stable key. Persisted as the promoted-hero choice, so do not rename. */
  id: string;
  label: string;
  value: string;
  unit?: string;
  subtext?: string;
  /** True when the value is modeled rather than measured. Rendered as an icon. */
  estimated?: boolean;
  confidence?: SpinQuality | null;
  /** Camera/radar preview. Rendered as an icon, not caption text. */
  experimental?: boolean;
  /** Override confidence copy while preserving its dot level. */
  confidenceLabel?: string;
}

/**
 * Live table: ten metrics, always the same ten in the same canonical order, so
 * the grid never reflows between shots. Metrics the shot did not produce render
 * as {@link NO_VALUE} rather than disappearing. The selected metric is then
 * pinned to the top-left by {@link pinSelectedMetric}.
 */
export const LIVE_METRIC_COUNT = 10;

/** Metric count for a swing-speed session: a single 5-tile row. */
export const SWING_METRIC_COUNT = 5;

function formatOptionalAngle(value: number | null, signed = false): string {
  if (value === null) return NO_VALUE;
  const prefix = signed && value >= 0 ? '+' : '';
  return `${prefix}${value.toFixed(1)}`;
}

function angleUnit(value: number | null): string | undefined {
  return value === null ? undefined : '°';
}

function formatSpinRpm(rpm: number | null): string {
  if (rpm === null) return NO_VALUE;
  return rpm.toLocaleString(getHtmlLang(), { maximumFractionDigits: 0 });
}

function launchAngleQuality(confidence: number | null): SpinQuality | null {
  if (confidence === null) return null;
  if (confidence >= 0.7) return 'high';
  if (confidence >= 0.4) return 'medium';
  return 'low';
}

function shotShape(spinAxisDeg: number | null): string | undefined {
  if (spinAxisDeg === null) return undefined;
  if (spinAxisDeg > 2) return t('shape.fade');
  if (spinAxisDeg < -2) return t('shape.draw');
  return t('shape.straight');
}

function markEstimated(isEstimated: boolean): true | undefined {
  return isEstimated ? true : undefined;
}

function experimentalStatus(status: string | null | undefined): string | undefined {
  if (!status || status === 'candidate_available') return undefined;
  return status.replace(/^rejected_/, 'rejected: ').replaceAll('_', ' ');
}

function buildBallStrikeMetrics(shot: Shot, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);
  const carry = shot.carry_spin_adjusted ?? shot.estimated_carry_yards;
  const angleConfidence = launchAngleQuality(shot.launch_angle_confidence);
  const angleEstimated = shot.angle_source === 'estimated';
  const horizontalLaunchIsCameraAssisted = shot.launch_angle_horizontal_source === 'camera_assisted_experimental';
  const fusedDeliveryAttempted = shot.experimental_fused_status != null;
  const attackAngle =
    shot.club_angle_deg ??
    shot.experimental_fused_attack_angle_deg ??
    (!fusedDeliveryAttempted ? shot.experimental_attack_angle_deg : null) ??
    null;
  const attackIsExperimental =
    shot.club_angle_deg === null &&
    (shot.experimental_fused_attack_angle_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_attack_angle_deg != null ||
      shot.experimental_attack_angle_status != null);
  const clubPath =
    shot.club_path_deg ??
    shot.experimental_fused_club_path_deg ??
    (!fusedDeliveryAttempted ? shot.experimental_club_path_deg : null) ??
    null;
  const clubPathIsExperimental =
    shot.club_path_deg === null &&
    (shot.experimental_fused_club_path_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_club_path_deg != null ||
      shot.experimental_club_path_status != null);

  return [
    {
      id: 'ball_speed',
      label: t('metric.ballSpeed'),
      value: formatSpeed(shot.ball_speed_mph, unitSystem, 1),
      unit: speedUnit,
    },
    {
      id: 'carry',
      label: t('metric.carry'),
      value: formatDistance(carry, unitSystem, 0),
      unit: getDistanceUnit(unitSystem),
      subtext: shot.carry_spin_adjusted === null ? undefined : t('metric.spinAdjusted'),
      estimated: markEstimated(shot.carry_spin_adjusted === null),
    },
    {
      id: 'club_speed',
      label: t('metric.clubSpeed'),
      value: shot.club_speed_mph === null ? NO_VALUE : formatSpeed(shot.club_speed_mph, unitSystem, 1),
      unit: shot.club_speed_mph === null ? undefined : speedUnit,
    },
    {
      id: 'smash',
      label: t('metric.smash'),
      value: shot.smash_factor === null ? NO_VALUE : shot.smash_factor.toFixed(2),
    },
    {
      id: 'launch_v',
      label: t('metric.vLaunch'),
      value: formatOptionalAngle(shot.launch_angle_vertical),
      unit: angleUnit(shot.launch_angle_vertical),
      estimated: markEstimated(shot.launch_angle_vertical !== null && angleEstimated),
      confidence: shot.launch_angle_vertical === null ? null : angleConfidence,
    },
    {
      id: 'launch_h',
      label: t('metric.hLaunch'),
      value: formatOptionalAngle(shot.launch_angle_horizontal, true),
      unit: angleUnit(shot.launch_angle_horizontal),
      subtext: horizontalLaunchIsCameraAssisted ? t('metric.cameraAssisted') : undefined,
      estimated: markEstimated(shot.launch_angle_horizontal !== null && angleEstimated),
      confidence: shot.launch_angle_horizontal === null ? null : angleConfidence,
      experimental: horizontalLaunchIsCameraAssisted || undefined,
    },
    {
      id: 'spin',
      label: t('metric.spin'),
      value: formatSpinRpm(shot.spin_rpm),
      unit: shot.spin_rpm === null ? undefined : 'rpm',
      estimated: markEstimated(shot.spin_rpm !== null && shot.spin_source === 'calculated'),
      confidence: shot.spin_rpm === null ? null : shot.spin_quality,
    },
    {
      id: 'spin_axis',
      label: t('metric.spinAxis'),
      value: formatOptionalAngle(shot.spin_axis_deg, true),
      unit: angleUnit(shot.spin_axis_deg),
      subtext: shotShape(shot.spin_axis_deg),
    },
    {
      id: 'club_path',
      label: t('metric.clubPath'),
      value: formatOptionalAngle(clubPath, true),
      unit: angleUnit(clubPath),
      subtext:
        shot.club_path_deg !== null
          ? undefined
          : fusedDeliveryAttempted
            ? shot.experimental_fused_club_path_deg != null
              ? t('metric.cameraFused')
              : experimentalStatus(shot.experimental_fused_status)
            : clubPathIsExperimental
              ? experimentalStatus(shot.experimental_club_path_status)
              : undefined,
      confidence: clubPathIsExperimental ? (shot.experimental_fused_club_path_confidence ?? null) : null,
      experimental: clubPathIsExperimental || undefined,
    },
    {
      id: 'club_aoa',
      label: t('metric.clubAoa'),
      value: formatOptionalAngle(attackAngle, true),
      unit: angleUnit(attackAngle),
      subtext:
        shot.club_angle_deg !== null
          ? undefined
          : fusedDeliveryAttempted
            ? shot.experimental_fused_attack_angle_deg != null
              ? t('metric.cameraFused')
              : experimentalStatus(shot.experimental_fused_status)
            : attackIsExperimental
              ? experimentalStatus(shot.experimental_attack_angle_status)
              : undefined,
      confidence: attackIsExperimental ? (shot.experimental_fused_attack_angle_confidence ?? null) : null,
      experimental: attackIsExperimental || undefined,
    },
  ];
}

function buildSwingSpeedMetrics(shot: Shot, stats: SwingSpeedStats, unitSystem: UnitSystem): LiveMetric[] {
  const speedUnit = getSpeedUnit(unitSystem);

  return [
    {
      id: 'swing_last',
      label: t('metric.lastSwing'),
      value: formatSpeed(getSwingSpeedMph(shot), unitSystem, 1),
      unit: speedUnit,
    },
    {
      id: 'swing_best',
      label: t('metric.best'),
      value: formatSpeed(stats.best_speed_mph, unitSystem, 1),
      unit: speedUnit,
      subtext: t('metric.profileImplement'),
    },
    {
      id: 'swing_avg',
      label: t('metric.average'),
      value: formatSpeed(stats.avg_speed_mph, unitSystem, 1),
      unit: speedUnit,
      subtext: t('metric.swingsCount', { count: stats.count }),
    },
    {
      id: 'swing_count',
      label: t('metric.swings'),
      value: String(stats.count),
      subtext:
        shot.swing_speed_reading_count === undefined
          ? undefined
          : t('metric.readingsCount', { count: shot.swing_speed_reading_count }),
    },
    {
      id: 'swing_implement',
      label: t('metric.implement'),
      value: shot.training_implement_label ?? shot.club,
      subtext:
        shot.swing_speed_trigger_mph === undefined
          ? undefined
          : t('metric.trigger', {
              speed: formatSpeed(shot.swing_speed_trigger_mph, unitSystem, 1),
              unit: speedUnit,
            }),
    },
  ];
}

/**
 * Build the fixed metric list for the Live panel. Returns {@link LIVE_METRIC_COUNT}
 * entries for a ball-strike shot and {@link SWING_METRIC_COUNT} for a swing-speed
 * one; the two sets share no ids, so a selected-metric choice never leaks across
 * modes.
 */
export function buildLiveMetrics(shot: Shot, unitSystem: UnitSystem, swingStats: SwingSpeedStats): LiveMetric[] {
  return isSwingSpeedShot(shot)
    ? buildSwingSpeedMetrics(shot, swingStats, unitSystem)
    : buildBallStrikeMetrics(shot, unitSystem);
}

/**
 * Put the selected metric first (top-left of the table) and keep the rest in
 * canonical order. Falls back to the first metric when `selectedId` is absent
 * from this list — which is the normal case right after switching between
 * ball-strike and swing-speed modes.
 */
export function pinSelectedMetric(metrics: LiveMetric[], selectedId: string | null): LiveMetric[] {
  if (metrics.length === 0) {
    return [];
  }

  const selectedIndex = metrics.findIndex((metric) => metric.id === selectedId);
  const index = selectedIndex === -1 ? 0 : selectedIndex;

  return [metrics[index], ...metrics.filter((_, i) => i !== index)];
}

/** Ball-missing overlay is a Live-tab concern; other panels have their own camera UI. */
export function shouldEnableLiveBallWarning(
  currentView: PanelView,
  camera: { available: boolean; enabled: boolean }
): boolean {
  return currentView === 'live' && camera.available && camera.enabled;
}

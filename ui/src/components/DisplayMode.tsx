import { useState } from 'react';
import type { CameraStatus } from '../stores/useCameraStore';
import type { Shot } from '../types/shot';
import { computeSwingSpeedStats, getSwingSpeedMph, isSwingSpeedShot } from '../types/shot';
import { useUnitPreference } from '../state/useUnitPreference';
import { formatDistance, formatSpeed, getDistanceUnit, getSpeedUnit } from '../utils/units';
import { getServerOrigin } from '../utils/serverOrigin';
import { MetricCard } from './ui/MetricCard';
import { getHtmlLang, type MessageKey } from '../i18n';
import { useI18n } from '../i18n/useI18n';
import './DisplayMode.css';

type Translate = (key: MessageKey, vars?: Record<string, string | number>) => string;

interface DisplayModeProps {
  connected: boolean;
  cameraStatus: CameraStatus;
  latestShot: Shot | null;
  shots: Shot[];
}

interface DisplayMetric {
  label: string;
  value: string;
  unit?: string;
  detail?: string;
  experimental?: boolean;
}

const CAMERA_STREAM_URL = `${getServerOrigin()}/camera/stream`;
const RECENT_SHOT_COUNT = 5;

function formatOptionalNumber(value: number | null, digits = 1, prefixPositive = false): string {
  if (value === null) {
    return '--';
  }

  const prefix = prefixPositive && value > 0 ? '+' : '';
  return `${prefix}${value.toFixed(digits)}`;
}

function formatSpin(value: number | null): string {
  if (value === null) {
    return '--';
  }

  return value.toLocaleString(getHtmlLang(), { maximumFractionDigits: 0 });
}

function experimentalStatus(status: string | null | undefined): string | undefined {
  if (!status || status === 'candidate_available') return undefined;
  return status.replace(/^rejected_/, 'rejected: ').replaceAll('_', ' ');
}

function buildMetrics(shot: Shot | null, unitSystem: 'imperial' | 'metric', t: Translate): DisplayMetric[] {
  if (!shot) {
    return [
      { label: t('display.ballSpeed'), value: '--', unit: getSpeedUnit(unitSystem) },
      { label: t('metric.carry'), value: '--', unit: getDistanceUnit(unitSystem) },
      { label: t('display.clubSpeed'), value: '--', unit: getSpeedUnit(unitSystem) },
      { label: t('metric.smash'), value: '--' },
      { label: t('display.launch'), value: '--', unit: 'deg' },
      { label: t('metric.spin'), value: '--', unit: 'rpm' },
      { label: t('display.clubPath'), value: '--', unit: 'deg' },
      { label: t('metric.clubAoa'), value: '--', unit: 'deg' },
      { label: t('metric.hLaunch'), value: '--', unit: 'deg' },
    ];
  }

  const carryYards = shot.carry_spin_adjusted ?? shot.estimated_carry_yards;
  const fusedDeliveryAttempted = shot.experimental_fused_status != null;
  const cameraAssistedLaunch = shot.launch_angle_horizontal_source === 'camera_assisted_experimental';
  const clubPathIsExperimental =
    shot.club_path_deg == null &&
    (shot.experimental_fused_club_path_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_club_path_deg != null ||
      shot.experimental_club_path_status != null);
  const attackIsExperimental =
    shot.club_angle_deg == null &&
    (shot.experimental_fused_attack_angle_deg != null ||
      shot.experimental_fused_status != null ||
      shot.experimental_attack_angle_deg != null ||
      shot.experimental_attack_angle_status != null);

  return [
    {
      label: t('display.ballSpeed'),
      value: formatSpeed(shot.ball_speed_mph, unitSystem, 1),
      unit: getSpeedUnit(unitSystem),
    },
    {
      label: t('metric.carry'),
      value: formatDistance(carryYards, unitSystem, 0),
      unit: getDistanceUnit(unitSystem),
      detail: shot.carry_spin_adjusted ? t('metric.spinAdjusted') : undefined,
    },
    {
      label: t('display.clubSpeed'),
      value: shot.club_speed_mph === null ? '--' : formatSpeed(shot.club_speed_mph, unitSystem, 1),
      unit: shot.club_speed_mph === null ? undefined : getSpeedUnit(unitSystem),
    },
    {
      label: t('metric.smash'),
      value: shot.smash_factor === null ? '--' : shot.smash_factor.toFixed(2),
    },
    {
      label: t('display.launch'),
      value: formatOptionalNumber(shot.launch_angle_vertical),
      unit: shot.launch_angle_vertical === null ? undefined : 'deg',
      detail: shot.angle_source ?? undefined,
    },
    {
      label: t('metric.spin'),
      value: formatSpin(shot.spin_rpm),
      unit: shot.spin_rpm === null ? undefined : 'rpm',
      detail: shot.spin_quality ?? undefined,
    },
    {
      label: t('display.clubPath'),
      value: formatOptionalNumber(
        shot.club_path_deg ??
          shot.experimental_fused_club_path_deg ??
          (!fusedDeliveryAttempted ? shot.experimental_club_path_deg : null) ??
          null,
        1,
        true
      ),
      unit:
        shot.club_path_deg == null &&
        shot.experimental_fused_club_path_deg == null &&
        (fusedDeliveryAttempted || shot.experimental_club_path_deg == null)
          ? undefined
          : 'deg',
      detail:
        shot.club_path_deg != null
          ? undefined
          : fusedDeliveryAttempted
            ? shot.experimental_fused_club_path_deg != null
              ? t('metric.cameraFused')
              : experimentalStatus(shot.experimental_fused_status)
            : shot.experimental_club_path_deg != null || shot.experimental_club_path_status != null
              ? experimentalStatus(shot.experimental_club_path_status)
              : undefined,
      experimental: clubPathIsExperimental || undefined,
    },
    {
      label: t('metric.clubAoa'),
      value: formatOptionalNumber(
        shot.club_angle_deg ??
          shot.experimental_fused_attack_angle_deg ??
          (!fusedDeliveryAttempted ? shot.experimental_attack_angle_deg : null) ??
          null
      ),
      unit:
        shot.club_angle_deg == null &&
        shot.experimental_fused_attack_angle_deg == null &&
        (fusedDeliveryAttempted || shot.experimental_attack_angle_deg == null)
          ? undefined
          : 'deg',
      detail:
        shot.club_angle_deg != null
          ? undefined
          : fusedDeliveryAttempted
            ? shot.experimental_fused_attack_angle_deg != null
              ? t('metric.cameraFused')
              : experimentalStatus(shot.experimental_fused_status)
            : shot.experimental_attack_angle_deg != null || shot.experimental_attack_angle_status != null
              ? experimentalStatus(shot.experimental_attack_angle_status)
              : undefined,
      experimental: attackIsExperimental || undefined,
    },
    {
      label: t('metric.hLaunch'),
      value: formatOptionalNumber(shot.launch_angle_horizontal, 1, true),
      unit: shot.launch_angle_horizontal === null ? undefined : 'deg',
      detail: cameraAssistedLaunch ? t('metric.cameraAssisted') : undefined,
      experimental: cameraAssistedLaunch || undefined,
    },
  ];
}

function toMetricCard(metric: DisplayMetric, featured = false) {
  return (
    <MetricCard
      key={metric.label}
      value={metric.value}
      unit={metric.unit}
      label={metric.label}
      subtext={metric.detail}
      experimental={metric.experimental}
      variant={featured ? 'emphasis' : 'default'}
    />
  );
}

export function DisplayMode({ connected, cameraStatus, latestShot, shots }: DisplayModeProps) {
  const { t } = useI18n();
  const [failedCameraKey, setFailedCameraKey] = useState<string | null>(null);
  const { unitSystem } = useUnitPreference();
  const isSwingSpeedSession = latestShot ? isSwingSpeedShot(latestShot) : false;
  const swingStats = computeSwingSpeedStats(shots);
  const metrics = isSwingSpeedSession
    ? [
        {
          label: t('display.lastSwing'),
          value: formatSpeed(swingStats.last_speed_mph, unitSystem, 1),
          unit: getSpeedUnit(unitSystem),
        },
        {
          label: t('metric.best'),
          value: formatSpeed(swingStats.best_speed_mph, unitSystem, 1),
          unit: getSpeedUnit(unitSystem),
          detail: t('display.thisSession'),
        },
        {
          label: t('metric.average'),
          value: formatSpeed(swingStats.avg_speed_mph, unitSystem, 1),
          unit: getSpeedUnit(unitSystem),
        },
        { label: t('metric.swings'), value: String(swingStats.count) },
      ]
    : buildMetrics(latestShot, unitSystem, t);
  const recentShots = shots.slice(-RECENT_SHOT_COUNT).reverse();
  const cameraKey = `${cameraStatus.available}-${cameraStatus.streaming}`;
  const cameraError = failedCameraKey === cameraKey;

  return (
    <main className="display-mode">
      <section className="display-mode__hero" aria-label={t('display.tvAria')}>
        <div className="display-mode__camera">
          {cameraError ? (
            <div className="display-mode__camera-placeholder">
              <span>{t('display.streamUnavailable')}</span>
            </div>
          ) : (
            <img
              src={CAMERA_STREAM_URL}
              alt={t('display.streamAlt')}
              className="display-mode__camera-image"
              onError={() => setFailedCameraKey(cameraKey)}
              onLoad={() => setFailedCameraKey(null)}
            />
          )}
          <div className="display-mode__status-row">
            <span
              className={`display-mode__status ${connected ? 'display-mode__status--online' : 'display-mode__status--offline'}`}
            >
              {connected ? t('display.socketOn') : t('display.socketOff')}
            </span>
            <span
              className={`display-mode__status ${cameraStatus.available && cameraStatus.streaming && !cameraError ? 'display-mode__status--online' : 'display-mode__status--offline'}`}
            >
              {cameraStatus.available && cameraStatus.streaming && !cameraError
                ? t('display.streamActive')
                : t('display.cameraUnavailable')}
            </span>
          </div>
        </div>

        <div className="display-mode__shot-panel">
          <div className="display-mode__eyebrow">{t('display.eyebrow')}</div>
          <h1 className="display-mode__title">
            {isSwingSpeedSession ? t('display.swingSpeed') : latestShot ? latestShot.club : t('display.ready')}
          </h1>
          <div className="display-mode__primary-grid">
            {toMetricCard(metrics[0], true)}
            {toMetricCard(metrics[1], true)}
          </div>
          <div className="display-mode__metrics-grid">{metrics.slice(2).map((metric) => toMetricCard(metric))}</div>
        </div>
      </section>

      <section className="display-mode__recent" aria-label={t('display.recentAria')}>
        {recentShots.length === 0 ? (
          <div className="display-mode__empty-strip">{t('display.recentEmpty')}</div>
        ) : (
          recentShots.map((shot, index) => (
            <div className="display-shot-chip" key={shot.timestamp}>
              <span className="display-shot-chip__number">#{shots.length - index}</span>
              <span className="display-shot-chip__club">
                {isSwingSpeedShot(shot) ? (shot.training_implement_label ?? shot.club) : shot.club}
              </span>
              <span className="display-shot-chip__stat">
                {formatSpeed(isSwingSpeedShot(shot) ? getSwingSpeedMph(shot) : shot.ball_speed_mph, unitSystem, 0)}{' '}
                {getSpeedUnit(unitSystem)}
              </span>
              {!isSwingSpeedShot(shot) && (
                <span className="display-shot-chip__stat">
                  {formatDistance(shot.carry_spin_adjusted ?? shot.estimated_carry_yards, unitSystem, 0)}{' '}
                  {getDistanceUnit(unitSystem)}
                </span>
              )}
            </div>
          ))
        )}
      </section>
    </main>
  );
}

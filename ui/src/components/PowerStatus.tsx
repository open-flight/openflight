import { useState } from 'react';
import { useSystemStore } from '../stores/useSystemStore';
import type { PowerStatus as PowerStatusData } from '../types/power';
import { useI18n } from '../i18n/useI18n';
import { batteryTone } from '../utils/batteryTone';
import './PowerStatus.css';

type WarningLevel = 'low' | 'critical';

function BatteryIcon({ status }: { status: PowerStatusData }) {
  const percent = Math.max(0, Math.min(100, status.battery_percent ?? 0));

  return (
    <span className="power-status__icon" aria-hidden="true">
      <span className="power-status__battery">
        <span className="power-status__fill" style={{ width: `${percent}%` }} />
      </span>
      {status.external_power ? (
        <svg className="power-status__bolt" viewBox="-3 -3 18 22" overflow="visible" aria-hidden="true">
          <path
            className="power-status__bolt-outline"
            d="M7.2 0 1 9h4l-.6 7L11 6.4H7.1L7.2 0Z"
            fill="none"
            stroke="var(--color-bg)"
            strokeWidth="3"
            strokeLinejoin="round"
            strokeLinecap="round"
            vectorEffect="non-scaling-stroke"
          />
          <path className="power-status__bolt-body" d="M7.2 0 1 9h4l-.6 7L11 6.4H7.1L7.2 0Z" fill="currentColor" />
        </svg>
      ) : null}
    </span>
  );
}

export function PowerIndicator({
  status,
  variant = 'chip',
}: {
  status: PowerStatusData;
  /** `chrome` sits in the footer next to units. */
  variant?: 'chip' | 'chrome';
}) {
  const { t } = useI18n();
  const percentage = status.battery_percent === null ? '--' : `${Math.round(status.battery_percent)}%`;
  const source = status.external_power ? t('power.pluggedIn') : t('power.onBattery');
  const label = status.available ? t('power.label', { source, percent: percentage }) : t('power.unavailable');
  const detail = status.available
    ? status.battery_voltage_v === null
      ? label
      : t('power.detailVolts', { label, volts: status.battery_voltage_v.toFixed(2) })
    : status.error || label;
  const tone = batteryTone(status.battery_percent);
  const charging = Boolean(status.external_power);
  const className = [
    'power-status',
    `power-status--${variant}`,
    `power-status--${status.state}`,
    `power-status--${tone}`,
    charging ? 'power-status--charging' : null,
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={className} aria-label={label} title={detail} role="status">
      <BatteryIcon status={status} />
      <span className="power-status__percentage">{percentage}</span>
    </div>
  );
}

export function PowerWarning({ level, percentage }: { level: WarningLevel; percentage: number }) {
  const { t } = useI18n();
  const [dismissed, setDismissed] = useState(false);
  if (dismissed) return null;

  const critical = level === 'critical';
  const roundedPercentage = Math.round(percentage);
  return (
    <div className="power-warning-overlay">
      <div
        className={`power-warning power-warning--${level}`}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="power-warning-title"
        aria-describedby="power-warning-detail"
      >
        <div className="power-warning__battery" aria-hidden="true">
          {`${roundedPercentage}%`}
        </div>
        <div className="power-warning__content">
          <h2 id="power-warning-title">{critical ? t('power.critical') : t('power.low')}</h2>
          <p id="power-warning-detail">{critical ? t('power.criticalDetail') : t('power.lowDetail')}</p>
        </div>
        <button type="button" onClick={() => setDismissed(true)} autoFocus>
          {t('power.dismiss')}
        </button>
      </div>
    </div>
  );
}

export function PowerExperience({
  status: statusProp,
  variant = 'chip',
}: {
  status?: PowerStatusData | null;
  variant?: 'chip' | 'chrome';
}) {
  const storeStatus = useSystemStore((state) => state.powerStatus);
  const status = statusProp ?? storeStatus;
  if (!status) return null;

  const warningLevel: WarningLevel | null =
    status.available && !status.external_power && (status.state === 'low' || status.state === 'critical')
      ? status.state
      : null;

  return (
    <>
      <PowerIndicator status={status} variant={variant} />
      {warningLevel && status.battery_percent !== null ? (
        <PowerWarning key={warningLevel} level={warningLevel} percentage={status.battery_percent} />
      ) : null}
    </>
  );
}

import { memo, useState } from 'react';
import type { DebugReading, RadarConfig, DebugShotLog } from '../types/socket';
import type { TriggerDiagnostic, TriggerStatus } from '../types/shot';
import './DebugPanel.css';

interface DebugPanelProps {
  enabled: boolean;
  readings: DebugReading[];
  shotLogs: DebugShotLog[];
  radarConfig: RadarConfig;
  mockMode: boolean;
  onToggle: () => void;
  onUpdateConfig: (config: Partial<RadarConfig>) => void;
  triggerDiagnostics: TriggerDiagnostic[];
  triggerStatus: TriggerStatus;
}

const REASON_DISPLAY: Record<string, string> = {
  accepted: 'Shot detected',
  no_response: 'No data from radar after trigger',
  parse_failed: 'Failed to parse radar data',
  no_outbound_speed: 'No outbound speed >= 15 mph',
  processing_failed: 'Failed to process capture data',
  shot_validation_failed: 'Ball speed too low for shot',
};

function formatReason(reason: string): string {
  return REASON_DISPLAY[reason] || reason;
}

function formatTimeAgo(timestamp: string): string {
  const now = Date.now();
  const then = new Date(timestamp).getTime();
  const diffSec = Math.floor((now - then) / 1000);

  if (diffSec < 5) return 'just now';
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  return `${Math.floor(diffSec / 3600)}h ago`;
}

function formatTime(timestamp: string): string {
  return new Date(timestamp).toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

interface SliderControlProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step?: number;
  unit?: string;
  disabled?: boolean;
  onChange: (value: number) => void;
}

function SliderControl({ label, value, min, max, step = 1, unit = '', disabled, onChange }: SliderControlProps) {
  const [localValue, setLocalValue] = useState(value);
  const [prevValue, setPrevValue] = useState(value);
  const [dragging, setDragging] = useState(false);

  if (prevValue !== value) {
    setPrevValue(value);
    if (!dragging) {
      setLocalValue(value);
    }
  }

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setDragging(true);
    setLocalValue(parseInt(e.target.value, 10));
  };

  const handleRelease = () => {
    setDragging(false);
    if (localValue !== value) {
      onChange(localValue);
    }
  };

  return (
    <div className={`slider-control ${disabled ? 'slider-control--disabled' : ''}`}>
      <div className="slider-control__header">
        <span className="slider-control__label">{label}</span>
        <span className="slider-control__value">
          {localValue}
          {unit}
        </span>
      </div>
      <input
        type="range"
        className="slider-control__input"
        min={min}
        max={max}
        step={step}
        value={localValue}
        disabled={disabled}
        onChange={handleChange}
        onMouseUp={handleRelease}
        onTouchEnd={handleRelease}
      />
      <div className="slider-control__range">
        <span>
          {min}
          {unit}
        </span>
        <span>
          {max}
          {unit}
        </span>
      </div>
    </div>
  );
}

interface TriggerRowProps {
  diag: TriggerDiagnostic;
}

const TriggerRow = memo(function TriggerRow({ diag }: TriggerRowProps) {
  return (
    <div className={`trigger-row ${diag.accepted ? 'trigger-row--accepted' : 'trigger-row--rejected'}`}>
      <div className="trigger-row__header">
        <span className="trigger-row__time">{formatTime(diag.timestamp)}</span>
        <span
          className={`trigger-row__badge ${diag.accepted ? 'trigger-row__badge--accepted' : 'trigger-row__badge--rejected'}`}
        >
          {diag.accepted ? 'HIT' : 'MISS'}
        </span>
      </div>
      <div className="trigger-row__details">
        <span className="trigger-row__reason">{formatReason(diag.reason)}</span>
        {diag.peak_outbound_mph > 0 && (
          <span className="trigger-row__speed">OUT {diag.peak_outbound_mph.toFixed(0)} mph</span>
        )}
        {diag.accepted && diag.ball_speed_mph && (
          <span className="trigger-row__ball-speed">{diag.ball_speed_mph.toFixed(0)} mph</span>
        )}
        {diag.iwr6843 && (
          <span className={`trigger-row__iwr trigger-row__iwr--${diag.iwr6843.state}`}>
            TI {diag.iwr6843.state.toUpperCase()}
            {diag.iwr6843.angle_deg !== undefined
              ? ` ${diag.iwr6843.angle_deg.toFixed(1)}°`
              : `: ${formatReason(diag.iwr6843.reason)}`}
          </span>
        )}
      </div>
    </div>
  );
});

function SystemStatus({ status }: { status: TriggerStatus }) {
  return (
    <div className="debug-panel__section">
      <h4>System Status</h4>
      <div className="system-status">
        <div className="system-status__item">
          <span className="system-status__label">Mode</span>
          <span className={`system-status__badge system-status__badge--${status.mode}`}>{status.mode}</span>
        </div>
        {status.trigger_type && (
          <div className="system-status__item">
            <span className="system-status__label">Trigger</span>
            <span className="system-status__value">{status.trigger_type}</span>
          </div>
        )}
        <div className="system-status__item">
          <span className="system-status__label">Radar</span>
          <span
            className={`system-status__value ${status.radar_connected ? 'system-status__value--success' : 'system-status__value--error'}`}
          >
            {status.radar_connected ? 'Connected' : 'Disconnected'}
          </span>
        </div>
        <div className="system-status__item">
          <span className="system-status__label">Triggers</span>
          <span className="system-status__value">
            <span className="system-status__counter">{status.triggers_total}</span>
            {status.triggers_total > 0 && (
              <>
                {' '}
                (<span className="system-status__counter--accepted">{status.triggers_accepted}</span>
                {' / '}
                <span className="system-status__counter--rejected">{status.triggers_rejected}</span>)
              </>
            )}
          </span>
        </div>
      </div>
    </div>
  );
}

function LastTriggerCard({ diag }: { diag: TriggerDiagnostic | null }) {
  if (!diag) {
    return (
      <div className="debug-panel__section">
        <h4>Last Trigger</h4>
        <p className="debug-panel__empty">Waiting for trigger...</p>
      </div>
    );
  }

  return (
    <div className="debug-panel__section">
      <h4>Last Trigger</h4>
      <div className={`last-trigger ${diag.accepted ? 'last-trigger--accepted' : 'last-trigger--rejected'}`}>
        <div className="last-trigger__header">
          <span
            className={`last-trigger__status ${diag.accepted ? 'last-trigger__status--accepted' : 'last-trigger__status--rejected'}`}
          >
            {diag.accepted ? 'ACCEPTED' : 'REJECTED'}
          </span>
          <span className="last-trigger__time">{formatTimeAgo(diag.timestamp)}</span>
        </div>

        <div className="last-trigger__reason">{formatReason(diag.reason)}</div>

        {diag.iwr6843 && (
          <div className={`last-trigger__iwr last-trigger__iwr--${diag.iwr6843.state}`}>
            <strong>TI radar: {diag.iwr6843.state}</strong>
            {diag.iwr6843.angle_deg !== undefined
              ? ` at ${diag.iwr6843.angle_deg.toFixed(1)}°`
              : ` — ${formatReason(diag.iwr6843.reason)}`}
          </div>
        )}

        <div className="last-trigger__data">
          <div className="last-trigger__speeds">
            <div className="last-trigger__speed-row">
              <span className="last-trigger__speed-label">Outbound</span>
              <span className="last-trigger__speed-value">
                {diag.outbound_readings} readings
                {diag.peak_outbound_mph > 0 && (
                  <>
                    , peak <strong>{diag.peak_outbound_mph.toFixed(1)} mph</strong>
                  </>
                )}
              </span>
            </div>
            <div className="last-trigger__speed-row">
              <span className="last-trigger__speed-label">Inbound</span>
              <span className="last-trigger__speed-value">
                {diag.inbound_readings} readings
                {diag.peak_inbound_mph > 0 && (
                  <>
                    , peak <strong>{diag.peak_inbound_mph.toFixed(1)} mph</strong>
                  </>
                )}
              </span>
            </div>
          </div>

          <div className="last-trigger__meta">
            {diag.latency_ms !== null && (
              <span className="last-trigger__meta-item">Latency: {diag.latency_ms.toFixed(0)}ms</span>
            )}
            {diag.response_bytes > 0 && (
              <span className="last-trigger__meta-item">Data: {(diag.response_bytes / 1024).toFixed(1)}KB</span>
            )}
            <span className="last-trigger__meta-item">Readings: {diag.total_readings}</span>
          </div>

          {diag.accepted && diag.ball_speed_mph && (
            <div className="last-trigger__shot-result">
              <div className="last-trigger__shot-item">
                <span className="last-trigger__shot-label">Ball</span>
                <span className="last-trigger__shot-value">{diag.ball_speed_mph.toFixed(1)} mph</span>
              </div>
              {diag.club_speed_mph && (
                <div className="last-trigger__shot-item">
                  <span className="last-trigger__shot-label">Club</span>
                  <span className="last-trigger__shot-value">{diag.club_speed_mph.toFixed(1)} mph</span>
                </div>
              )}
              {diag.spin_rpm && (
                <div className="last-trigger__shot-item">
                  <span className="last-trigger__shot-label">Spin</span>
                  <span className="last-trigger__shot-value">{diag.spin_rpm.toFixed(0)} rpm</span>
                </div>
              )}
              {diag.carry_yards && (
                <div className="last-trigger__shot-item">
                  <span className="last-trigger__shot-label">Carry</span>
                  <span className="last-trigger__shot-value">{diag.carry_yards.toFixed(0)} yds</span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

type DebugTab = 'status' | 'history' | 'tuning';

export function DebugPanel({
  radarConfig,
  mockMode,
  onUpdateConfig,
  triggerDiagnostics,
  triggerStatus,
}: DebugPanelProps) {
  const [activeTab, setActiveTab] = useState<DebugTab>('status');
  const isRollingBuffer = triggerStatus.mode === 'rolling-buffer';
  const isSwingSpeed = triggerStatus.mode === 'swing-speed';
  const tuningDisabled = mockMode && !isSwingSpeed;
  const lastDiag = triggerDiagnostics.length > 0 ? triggerDiagnostics[triggerDiagnostics.length - 1] : null;

  // Show last 20 triggers, newest first
  const recentTriggers = [...triggerDiagnostics].reverse().slice(0, 20);

  return (
    <div className="debug-panel">
      <div className="debug-panel__header">
        <h3>Diagnostics</h3>
      </div>

      <div className="debug-tabs">
        <button
          className={`debug-tabs__tab ${activeTab === 'status' ? 'debug-tabs__tab--active' : ''}`}
          onClick={() => setActiveTab('status')}
        >
          Status
        </button>
        {isRollingBuffer && (
          <button
            className={`debug-tabs__tab ${activeTab === 'history' ? 'debug-tabs__tab--active' : ''}`}
            onClick={() => setActiveTab('history')}
          >
            History
          </button>
        )}
        <button
          className={`debug-tabs__tab ${activeTab === 'tuning' ? 'debug-tabs__tab--active' : ''}`}
          onClick={() => setActiveTab('tuning')}
        >
          Tuning
        </button>
      </div>

      <div className="debug-panel__tab-content">
        {activeTab === 'status' && (
          <>
            <SystemStatus status={triggerStatus} />
            {isRollingBuffer && <LastTriggerCard diag={lastDiag} />}
            {!isRollingBuffer && triggerStatus.mode !== 'mock' && (
              <div className="debug-panel__section">
                <p className="debug-panel__hint">
                  Trigger diagnostics are available in rolling buffer mode. Current mode: {triggerStatus.mode}
                </p>
              </div>
            )}
          </>
        )}

        {activeTab === 'history' && isRollingBuffer && (
          <div className="debug-panel__section debug-panel__section--history">
            <h4>Trigger History</h4>
            <div className="trigger-history">
              {recentTriggers.length === 0 ? (
                <p className="debug-panel__empty">No triggers yet...</p>
              ) : (
                recentTriggers.map((diag, index) => <TriggerRow key={`${diag.timestamp}-${index}`} diag={diag} />)
              )}
            </div>
          </div>
        )}

        {activeTab === 'tuning' && (
          <div className="debug-panel__section">
            <h4>Radar Tuning</h4>
            {tuningDisabled && <p className="debug-panel__mock-warning">Radar tuning disabled in mock mode</p>}
            {mockMode && isSwingSpeed && (
              <p className="debug-panel__mock-warning">Mock swing speed sliders shape simulated reps only</p>
            )}
            <div className="debug-panel__controls">
              {isSwingSpeed ? (
                <>
                  <SliderControl
                    label="Lower Speed"
                    value={radarConfig.min_speed}
                    min={30}
                    max={100}
                    unit=" mph"
                    disabled={false}
                    onChange={(v) => onUpdateConfig({ min_speed: v })}
                  />
                  <SliderControl
                    label="Upper Speed"
                    value={radarConfig.max_speed}
                    min={90}
                    max={170}
                    unit=" mph"
                    disabled={false}
                    onChange={(v) => onUpdateConfig({ max_speed: v })}
                  />
                </>
              ) : (
                <>
                  <SliderControl
                    label="Min Speed"
                    value={radarConfig.min_speed}
                    min={0}
                    max={50}
                    unit=" mph"
                    disabled={tuningDisabled}
                    onChange={(v) => onUpdateConfig({ min_speed: v })}
                  />
                  <SliderControl
                    label="Min Magnitude"
                    value={radarConfig.min_magnitude}
                    min={0}
                    max={2000}
                    step={50}
                    disabled={tuningDisabled}
                    onChange={(v) => onUpdateConfig({ min_magnitude: v })}
                  />
                </>
              )}
              <SliderControl
                label="TX Power"
                value={radarConfig.transmit_power}
                min={0}
                max={7}
                disabled={mockMode}
                onChange={(v) => onUpdateConfig({ transmit_power: v })}
              />
            </div>
            <p className="debug-panel__hint">
              {isSwingSpeed
                ? 'Lower speed updates the OPS filter; upper speed rejects implausible high swing-speed outliers.'
                : 'TX Power: 0 = max range, 7 = min range'}
            </p>
          </div>
        )}
      </div>
    </div>
  );
}

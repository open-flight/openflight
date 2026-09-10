import { useState, type ReactNode } from 'react';
import type { CameraStatus } from '../../stores/useCameraStore';
import { useCameraStore } from '../../stores/useCameraStore';
import { useDebugStore } from '../../stores/useDebugStore';
import { useLaunchDaddyStore } from '../../stores/useLaunchDaddyStore';
import { useSystemStore } from '../../stores/useSystemStore';
import type { SimStatus } from '../../types/socket';
import { useI18n } from '../../i18n/useI18n';
import { ballDetectionStatusLabel } from '../../utils/ballDetectionStatus';
import { StatusMenu } from './StatusMenu';

interface PanelHeaderProps {
  /** Uppercase panel name, e.g. "Live". */
  title: string;
  /** Secondary text after the hairline divider, e.g. the profile or shot count. */
  subtitle?: ReactNode;
  /** Active club or training implement, shown after the subtitle. */
  club?: ReactNode;
  /** Right-hand `PanelAction` buttons: primary, secondary, or danger. */
  actions?: ReactNode;
  /**
   * Socket connection. Omit to read `useSystemStore`; pass it in tests so SSR
   * is not stuck with the store's initial `false`.
   */
  connected?: boolean;
  /** OPS243 link from `trigger_status`. Omit to read `useDebugStore`. */
  radarConnected?: boolean;
  /** Camera / YOLO snapshot. Omit to read `useCameraStore`. */
  cameraStatus?: CameraStatus;
  /** Simulator connectors. Omit to read `useSystemStore`. */
  simStatuses?: Record<string, SimStatus>;
  /**
   * Force the status menu open or closed. Omit to toggle from the LED + title
   * tap (the path the kiosk uses).
   */
  statusMenuOpen?: boolean;
}

/**
 * Hairline divider plus muted identity text. Used for the profile/status
 * subtitle and the active club so both stay visually in the same row.
 */
function IdentityPart({ children, className }: { children: ReactNode; className: string }) {
  return (
    <>
      <span className="panel-header__divider" aria-hidden="true" />
      <span className={className}>{children}</span>
    </>
  );
}

/**
 * Page chrome: title plus a connection LED on the left, and shutdown on the
 * right. Tapping the LED and title opens a status menu (server, radar, ball
 * detection, simulators). Five taps still toggle Launch Daddy, which used to live on this
 * LED alone. Header actions sit to the left of shutdown, separated by a
 * hairline divider.
 */
export function PanelHeader({
  title,
  subtitle,
  club,
  actions,
  connected: connectedProp,
  radarConnected: radarConnectedProp,
  cameraStatus: cameraStatusProp,
  simStatuses: simStatusesProp,
  statusMenuOpen: statusMenuOpenProp,
}: PanelHeaderProps) {
  const { t } = useI18n();
  const storeConnected = useSystemStore((state) => state.connected);
  const storeRadarConnected = useDebugStore((state) => state.triggerStatus.radar_connected);
  const storeCameraStatus = useCameraStore((state) => state.cameraStatus);
  const storeSimStatuses = useSystemStore((state) => state.simStatuses);
  const handleSecretTap = useLaunchDaddyStore((state) => state.handleSecretTap);
  const [internalOpen, setInternalOpen] = useState(false);

  const connected = connectedProp ?? storeConnected;
  const radarConnected = radarConnectedProp ?? storeRadarConnected;
  const cameraStatus = cameraStatusProp ?? storeCameraStatus;
  const simStatuses = simStatusesProp ?? storeSimStatuses;
  const menuOpen = statusMenuOpenProp ?? internalOpen;
  const status = connected ? 'connected' : 'disconnected';
  const statusLabel = connected ? t('header.serverConnected') : t('header.serverDisconnected');

  const toggleMenu = () => {
    handleSecretTap();
    if (statusMenuOpenProp === undefined) {
      setInternalOpen((open) => !open);
    }
  };

  return (
    <header className="panel-header">
      <div className="panel-header__identity">
        <button
          type="button"
          className="panel-header__status"
          onClick={toggleMenu}
          aria-label={statusLabel}
          aria-haspopup="dialog"
          aria-expanded={menuOpen}
        >
          <span className={`panel-header__dot panel-header__dot--${status}`} aria-hidden="true" />
          <span className="panel-header__title">{title}</span>
        </button>
        {subtitle ? <IdentityPart className="panel-header__subtitle">{subtitle}</IdentityPart> : null}
        {club ? <IdentityPart className="panel-header__club">{club}</IdentityPart> : null}
      </div>
      <div className="panel-header__actions">
        {actions ? (
          <>
            {actions}
            <span className="panel-header__divider" aria-hidden="true" />
          </>
        ) : null}
        <button
          type="button"
          className="panel-header__power"
          onClick={() => useSystemStore.getState().openShutdownDialog()}
          aria-label={t('menu.shutdown')}
          title={t('menu.shutdown')}
        >
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" aria-hidden="true">
            <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
            <line x1="12" y1="2" x2="12" y2="12" />
          </svg>
        </button>
      </div>
      {menuOpen ? (
        <StatusMenu
          connected={connected}
          radarConnected={radarConnected}
          ballDetection={ballDetectionStatusLabel(cameraStatus)}
          simStatuses={simStatuses}
          onClose={() => {
            if (statusMenuOpenProp === undefined) {
              setInternalOpen(false);
            }
          }}
        />
      ) : null}
    </header>
  );
}

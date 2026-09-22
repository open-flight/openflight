import { useState, type ReactNode } from 'react';
import { useDebugStore } from '../../stores/useDebugStore';
import { useLaunchDaddyStore } from '../../stores/useLaunchDaddyStore';
import { useSystemStore } from '../../stores/useSystemStore';
import { useI18n } from '../../i18n/useI18n';
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
 * Page chrome: title plus a connection LED. Tapping the LED and title opens a
 * status menu (server, radar, ball detection). Five taps still toggle Launch
 * Daddy, which used to live on this LED alone.
 */
export function PanelHeader({
  title,
  subtitle,
  club,
  actions,
  connected: connectedProp,
  radarConnected: radarConnectedProp,
  statusMenuOpen: statusMenuOpenProp,
}: PanelHeaderProps) {
  const { t } = useI18n();
  const storeConnected = useSystemStore((state) => state.connected);
  const storeRadarConnected = useDebugStore((state) => state.triggerStatus.radar_connected);
  const handleSecretTap = useLaunchDaddyStore((state) => state.handleSecretTap);
  const [internalOpen, setInternalOpen] = useState(false);

  const connected = connectedProp ?? storeConnected;
  const radarConnected = radarConnectedProp ?? storeRadarConnected;
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
      {actions ? <div className="panel-header__actions">{actions}</div> : null}
      {menuOpen ? (
        <StatusMenu
          connected={connected}
          radarConnected={radarConnected}
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

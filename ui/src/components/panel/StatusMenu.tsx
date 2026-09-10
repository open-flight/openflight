import { useLayoutEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useI18n } from '../../i18n/useI18n';
import { useSystemStore } from '../../stores/useSystemStore';
import type { SimStatus as SimStatusData } from '../../types/socket';
import { SimStatus } from '../SimStatus';

interface StatusMenuProps {
  connected: boolean;
  radarConnected: boolean;
  ballDetection: string;
  simStatuses?: Record<string, SimStatusData>;
  onClose: () => void;
}

function OverlayOnApp({ children }: { children: ReactNode }) {
  const [host, setHost] = useState<Element | null>(null);

  useLayoutEffect(() => {
    // After commit, `.panel-app` is in the document. Looking it up here (not
    // during render) is how the status dim shares the footer menu's scrim.
    // eslint-disable-next-line react-hooks/set-state-in-effect -- portal host exists only after layout
    setHost(document.querySelector('.panel-app'));
  }, []);

  if (!host) {
    return children;
  }
  return createPortal(children, host);
}

/**
 * Compact system readout anchored under the panel header LED + title.
 * Portaled onto `.panel-app` so the dim uses the same `.panel-scrim` as the
 * footer menu (absolute inset covering the whole kiosk, not just the header).
 * Simulator connector pills appear only after at least one `sim_status` event.
 */
export function StatusMenu({
  connected,
  radarConnected,
  ballDetection,
  simStatuses: simStatusesProp,
  onClose,
}: StatusMenuProps) {
  const { t } = useI18n();
  const storeSimStatuses = useSystemStore((state) => state.simStatuses);
  const simStatuses = simStatusesProp ?? storeSimStatuses;
  const hasSimulators = Object.keys(simStatuses).length > 0;
  const linkValue = (ok: boolean) => (ok ? t('header.connected') : t('header.disconnected'));

  return (
    <OverlayOnApp>
      <button type="button" className="panel-scrim" onClick={onClose} aria-label={t('header.closeStatus')} />
      <div className="panel-header__status-menu" role="dialog" aria-label={t('header.statusMenu')}>
        <div className="panel-header__status-row">
          <span className="panel-header__status-label">{t('header.server')}</span>
          <span className="panel-header__status-value">{linkValue(connected)}</span>
        </div>
        <div className="panel-header__status-row">
          <span className="panel-header__status-label">{t('header.radar')}</span>
          <span className="panel-header__status-value">{linkValue(radarConnected)}</span>
        </div>
        <div className="panel-header__status-row">
          <span className="panel-header__status-label">{t('menu.ballDetection')}</span>
          <span className="panel-header__status-value">{ballDetection}</span>
        </div>
        {hasSimulators ? (
          <div className="panel-header__status-sims">
            <span className="panel-header__status-label">{t('menu.simulators')}</span>
            <SimStatus statuses={simStatuses} />
          </div>
        ) : null}
      </div>
    </OverlayOnApp>
  );
}

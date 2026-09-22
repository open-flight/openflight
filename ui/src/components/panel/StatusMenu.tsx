import { useLayoutEffect, useState, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import { useI18n } from '../../i18n/useI18n';

interface StatusMenuProps {
  connected: boolean;
  radarConnected: boolean;
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
 */
export function StatusMenu({ connected, radarConnected, onClose }: StatusMenuProps) {
  const { t } = useI18n();
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
      </div>
    </OverlayOnApp>
  );
}

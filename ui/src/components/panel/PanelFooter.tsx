import type { ReactNode } from 'react';
import Logo from '../../logo/Logo';
import { TabBar } from '../ui/TabBar';
import type { PanelView } from './views';
import { PANEL_VIEWS } from './views';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';
import { useUnitPreference } from '../../state/useUnitPreference';
import { getUnitsLabel } from '../../utils/units';
import { useSystemStore } from '../../stores/useSystemStore';
import { PowerExperience } from '../PowerStatus';
import type { PowerStatus } from '../../types/power';

interface PanelFooterProps {
  currentView: PanelView;
  onChangeView: (view: PanelView) => void;
  onOpenMenu: () => void;
  menuOpen: boolean;
  shotCount: number;
  cameraStreaming: boolean;
  ballDetected: boolean;
  debugRecording: boolean;
  /** Replaces the logo when Launch Daddy mode is active. */
  brand?: ReactNode;
  /**
   * Battery telemetry. Omit to read `useSystemStore`. Pass it in tests because
   * `renderToString` keeps the store's server snapshot at `null`.
   */
  powerStatus?: PowerStatus | null;
}

const VIEWS_WITH_UNITS: ReadonlySet<PanelView> = new Set(['live', 'stats', 'shots']);

/**
 * Bottom bar: menu button, divider-separated panel tabs, and view meta on the
 * right. Panel actions and shutdown live in `PanelHeader`.
 *
 * The mockup footer shows four tabs; Profiles and Debug are extra working
 * screens. Burying either behind a gesture makes them unreachable on the kiosk.
 */
export function PanelFooter({
  currentView,
  onChangeView,
  onOpenMenu,
  menuOpen,
  shotCount,
  cameraStreaming,
  ballDetected,
  debugRecording,
  brand,
  powerStatus: powerStatusProp,
}: PanelFooterProps) {
  const { t } = useI18n();
  const { unitSystem } = useUnitPreference();
  const unitsLabel = getUnitsLabel(unitSystem);
  const showUnits = VIEWS_WITH_UNITS.has(currentView);
  const storePowerStatus = useSystemStore((state) => state.powerStatus);
  const powerStatus = powerStatusProp !== undefined ? powerStatusProp : storePowerStatus;
  const options = PANEL_VIEWS.map((view) => {
    const label = t(`nav.${view.id}` as MessageKey);
    switch (view.id) {
      case 'shots':
        return {
          ...view,
          label,
          badge: shotCount > 0 ? <span className="nav__badge">{shotCount}</span> : undefined,
        };
      case 'camera':
        return {
          ...view,
          label,
          extraClassName: cameraStreaming ? 'nav__button--streaming' : undefined,
          badge: ballDetected ? <span className="nav__ball-dot" /> : undefined,
        };
      case 'debug':
        return {
          ...view,
          label,
          extraClassName: debugRecording ? 'nav__button--recording' : undefined,
          badge: debugRecording ? <span className="nav__recording-dot" /> : undefined,
        };
      default:
        return { ...view, label };
    }
  });

  return (
    <div className="panel-footer">
      <button
        type="button"
        className="panel-footer__menu"
        style={{ border: 'none' }}
        onClick={onOpenMenu}
        aria-expanded={menuOpen}
        aria-label={t('nav.openMenu')}
      >
        {brand ?? <Logo size="small" variant="mono" />}
      </button>

      <div className="panel-footer__nav">
        <TabBar
          className="panel-footer__tabs"
          ariaLabel={t('nav.panels')}
          value={currentView}
          onChange={onChangeView}
          options={options}
          separator={<span className="panel-header__divider" aria-hidden="true" />}
        />
      </div>

      {showUnits || powerStatus ? (
        <div className="panel-footer__meta">
          {showUnits ? <span className="panel-footer__units">{unitsLabel}</span> : null}
          {powerStatus ? <PowerExperience status={powerStatus} variant="chrome" /> : null}
        </div>
      ) : null}
    </div>
  );
}

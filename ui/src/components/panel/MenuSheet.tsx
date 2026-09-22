import { LOCALES, type LocaleId } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { useSystemStore } from '../../stores/useSystemStore';
import { useThemeStore } from '../../stores/useThemeStore';
import { useLocaleStore } from '../../stores/useLocaleStore';
import { useUnitPreference } from '../../state/useUnitPreference';
import { SegmentedControl } from '../ui/SegmentedControl';
import { SimStatus } from '../SimStatus';

interface MenuSheetProps {
  onClose: () => void;
  onShutdown: () => void;
}

/**
 * The sheet behind the footer logo button (design doc 6a `menuOpen6`).
 *
 * 6a draws Units / Shut down. Profiles live on their own panel. The System
 * block is an addition: the mockup replaced the old top header, and simulator
 * state had nowhere else to go. Battery lives in the footer.
 * Socket connection lives on the panel header LED.
 */
export function MenuSheet({ onClose, onShutdown }: MenuSheetProps) {
  const simStatuses = useSystemStore((state) => state.simStatuses);
  const { t } = useI18n();
  const { unitSystem, setUnitSystem } = useUnitPreference();
  const { theme, setTheme } = useThemeStore();
  const { locale, setLocale } = useLocaleStore();

  return (
    <>
      <button type="button" className="panel-scrim" onClick={onClose} aria-label={t('menu.close')} />
      <div className="menu-sheet" role="dialog" aria-modal="true" aria-label={t('menu.title')}>
        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.units')}</span>
          <SegmentedControl
            ariaLabel={t('menu.displayUnits')}
            value={unitSystem}
            options={[
              { id: 'imperial', label: 'MPH / YDS' },
              { id: 'metric', label: 'KMH / M' },
            ]}
            onChange={setUnitSystem}
          />
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.theme')}</span>
          <SegmentedControl
            ariaLabel={t('menu.theme')}
            value={theme}
            options={[
              { id: 'dark', label: t('menu.themeDark') },
              { id: 'light', label: t('menu.themeLight') },
            ]}
            onChange={setTheme}
          />
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.language')}</span>
          <select
            className="menu-sheet__select"
            aria-label={t('menu.language')}
            value={locale}
            onChange={(event) => setLocale(event.target.value as LocaleId)}
          >
            {LOCALES.map((option) => (
              <option key={option.id} value={option.id}>
                {option.nativeName}
              </option>
            ))}
          </select>
        </section>

        <section className="menu-sheet__section">
          <span className="menu-sheet__section-title">{t('menu.system')}</span>
          {Object.keys(simStatuses).length > 0 ? (
            <div className="menu-sheet__status-row">
              <span className="menu-sheet__status-label">{t('menu.simulators')}</span>
              <SimStatus statuses={simStatuses} />
            </div>
          ) : null}
        </section>

        <button type="button" className="menu-sheet__shutdown" onClick={onShutdown}>
          {t('menu.shutdown')}
        </button>
      </div>
    </>
  );
}

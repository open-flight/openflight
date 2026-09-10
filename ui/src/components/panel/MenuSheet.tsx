import { useRef } from 'react';
import { LOCALES, type LocaleId } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { useDragScroll } from '../../hooks/useDragScroll';
import { useThemeStore } from '../../stores/useThemeStore';
import { useLocaleStore } from '../../stores/useLocaleStore';
import { isLiveViewDurationMs, useLiveViewStore } from '../../stores/useLiveViewStore';
import { useUnitPreference } from '../../state/useUnitPreference';
import { SegmentedControl } from '../ui/SegmentedControl';

interface MenuSheetProps {
  onClose: () => void;
}

/**
 * The sheet behind the footer logo button (design doc 6a `menuOpen6`).
 *
 * 6a draws Units / Shut down. Profiles live on their own panel. Battery lives
 * in the footer. Socket connection lives on the panel header LED. Shutdown is
 * the header power button. Ball detection and simulators stay in the header
 * status menu.
 */
export function MenuSheet({ onClose }: MenuSheetProps) {
  const { t } = useI18n();
  const { unitSystem, setUnitSystem } = useUnitPreference();
  const { theme, setTheme } = useThemeStore();
  const { locale, setLocale } = useLocaleStore();
  useLiveViewStore((state) => state.mode);
  useLiveViewStore((state) => state.durationMs);
  const { mode, durationMs, setMode, setDurationMs } = useLiveViewStore.getState();
  const sheetRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(sheetRef);

  return (
    <>
      <button type="button" className="panel-scrim" onClick={onClose} aria-label={t('menu.close')} />
      <div
        ref={sheetRef}
        className="menu-sheet"
        role="dialog"
        aria-modal="true"
        aria-label={t('menu.title')}
        onPointerDown={dragScroll.onPointerDown}
        onPointerMove={dragScroll.onPointerMove}
        onPointerUp={dragScroll.onPointerUp}
        onPointerCancel={dragScroll.onPointerCancel}
        onClickCapture={dragScroll.onClickCapture}
      >
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
          <span className="menu-sheet__section-title">{t('menu.liveView')}</span>
          <SegmentedControl
            ariaLabel={t('menu.liveView')}
            value={mode}
            options={[
              { id: 'tiles', label: t('menu.liveTiles') },
              { id: 'timed', label: t('menu.liveTimed') },
              { id: 'sticky', label: t('menu.liveHold') },
            ]}
            onChange={setMode}
          />
          {mode === 'timed' ? (
            <SegmentedControl
              ariaLabel={t('onboarding.duration')}
              value={String(durationMs)}
              options={[
                { id: '5000', label: t('onboarding.duration5') },
                { id: '10000', label: t('onboarding.duration10') },
                { id: '15000', label: t('onboarding.duration15') },
              ]}
              onChange={(id) => {
                const next = Number(id);
                if (isLiveViewDurationMs(next)) {
                  setDurationMs(next);
                }
              }}
            />
          ) : null}
        </section>
      </div>
    </>
  );
}

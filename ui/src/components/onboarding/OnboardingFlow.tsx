import { useState, type MouseEventHandler, type ReactNode } from 'react';
import { LOCALES } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';
import { useUnitPreference } from '../../state/useUnitPreference';
import {
  isLiveViewDurationMs,
  LIVE_VIEW_DURATIONS_MS,
  useLiveViewStore,
  type LiveViewDurationMs,
  type LiveViewMode,
} from '../../stores/useLiveViewStore';
import { useLocaleStore } from '../../stores/useLocaleStore';
import { useOnboardingStore } from '../../stores/useOnboardingStore';
import { useThemeStore } from '../../stores/useThemeStore';
import './OnboardingFlow.css';

export type OnboardingStep = 'welcome' | 'language' | 'theme' | 'live' | 'done';

const STEPS = ['welcome', 'language', 'theme', 'live', 'done'] as const;

const DURATION_KEYS: Record<
  LiveViewDurationMs,
  'onboarding.duration5' | 'onboarding.duration10' | 'onboarding.duration15'
> = {
  5000: 'onboarding.duration5',
  10000: 'onboarding.duration10',
  15000: 'onboarding.duration15',
};

const LIVE_MODES: ReadonlyArray<{
  id: LiveViewMode;
  label: 'onboarding.liveTiles' | 'onboarding.liveTimed' | 'onboarding.liveHold';
  detail: 'onboarding.liveTilesDetail' | 'onboarding.liveTimedDetail' | 'onboarding.liveHoldDetail';
}> = [
  { id: 'tiles', label: 'onboarding.liveTiles', detail: 'onboarding.liveTilesDetail' },
  { id: 'timed', label: 'onboarding.liveTimed', detail: 'onboarding.liveTimedDetail' },
  { id: 'sticky', label: 'onboarding.liveHold', detail: 'onboarding.liveHoldDetail' },
];

function MiniGrid() {
  return (
    <span className="onboarding__mini-grid">
      {Array.from({ length: 6 }, (_, index) => (
        <span key={index} className={`onboarding__mini-cell${index === 0 ? ' onboarding__mini-cell--selected' : ''}`} />
      ))}
    </span>
  );
}

function ThemeSwatch({ appearance }: { appearance: 'dark' | 'light' }) {
  return (
    <span className={`onboarding__theme-swatch onboarding__theme-swatch--${appearance}`} aria-hidden="true">
      <MiniGrid />
    </span>
  );
}

function LiveViewDemo({ mode }: { mode: LiveViewMode }) {
  return (
    <span className={`onboarding__live-demo onboarding__live-demo--${mode}`} aria-hidden="true">
      <MiniGrid />
      {mode === 'tiles' ? null : (
        <span className="onboarding__live-overlay">
          <span className="onboarding__live-overlay-label">mph</span>
          <span className="onboarding__live-overlay-value">167</span>
          {mode === 'sticky' ? <span className="onboarding__live-tap" /> : null}
        </span>
      )}
    </span>
  );
}

function Tile({
  selected,
  onClick,
  label,
  detail,
  appearance,
  preview,
  footer,
}: {
  selected: boolean;
  onClick: MouseEventHandler<HTMLButtonElement>;
  label: string;
  detail?: string;
  appearance?: 'dark' | 'light';
  preview?: ReactNode;
  footer?: ReactNode;
}) {
  const classes = [
    'onboarding__tile',
    selected ? 'onboarding__tile--selected' : '',
    appearance ? `onboarding__tile--${appearance}` : '',
    preview ? 'onboarding__tile--preview' : '',
  ]
    .filter(Boolean)
    .join(' ');

  return (
    <div className={classes}>
      <button type="button" className="onboarding__tile-hit" aria-pressed={selected} onClick={onClick}>
        {preview}
        <span className="onboarding__tile-copy">
          <span className="onboarding__tile-label">{label}</span>
          {detail ? <span className="onboarding__tile-detail">{detail}</span> : null}
        </span>
      </button>
      {footer}
    </div>
  );
}

export function OnboardingFlow({
  onFinished,
  initialStep = 'welcome',
}: {
  onFinished: () => void;
  initialStep?: OnboardingStep;
}) {
  const [step, setStep] = useState<OnboardingStep>(initialStep);
  const { t } = useI18n();
  const { locale, setLocale } = useLocaleStore();
  const { theme, setTheme } = useThemeStore();
  const { unitSystem, setUnitSystem } = useUnitPreference();
  useLiveViewStore((state) => state.mode);
  useLiveViewStore((state) => state.durationMs);
  const { mode, durationMs, setMode, setDurationMs } = useLiveViewStore.getState();
  const complete = useOnboardingStore((state) => state.complete);

  const stepIndex = STEPS.indexOf(step);
  const current = stepIndex + 1;

  function goNext() {
    const next = STEPS[stepIndex + 1];
    if (next) {
      setStep(next);
    }
  }

  function goBack() {
    const prev = STEPS[stepIndex - 1];
    if (prev) {
      setStep(prev);
    }
  }

  function handleStart() {
    complete();
    onFinished();
  }

  function handleDuration(id: string) {
    const next = Number(id);
    if (isLiveViewDurationMs(next)) {
      setDurationMs(next);
    }
  }

  return (
    <div className="onboarding" role="dialog" aria-modal="true" aria-labelledby="onboarding-title">
      <div className="onboarding__body">
        {step === 'welcome' ? (
          <div className="onboarding__welcome onboarding__welcome--start">
            <div className="onboarding__mark" aria-hidden="true">
              <img className="onboarding__mark-logo" src="/openflightlogo.svg" alt="" />
            </div>
            <h1 id="onboarding-title" className="onboarding__title onboarding__title--visually-hidden">
              {t('onboarding.welcomeTitle')}
            </h1>
            <button type="button" className="onboarding__primary" onClick={goNext}>
              {t('onboarding.getStarted')}
            </button>
          </div>
        ) : null}

        {step === 'language' ? (
          <>
            <h1 id="onboarding-title" className="onboarding__title">
              {t('onboarding.languageTitle')}
            </h1>
            <div className="onboarding__grid onboarding__grid--locales">
              {LOCALES.map((option) => (
                <Tile
                  key={option.id}
                  selected={locale === option.id}
                  label={option.nativeName}
                  onClick={() => setLocale(option.id)}
                />
              ))}
            </div>
            <h2 className="onboarding__subtitle">{t('onboarding.unitsTitle')}</h2>
            <div className="onboarding__grid onboarding__grid--units">
              <Tile selected={unitSystem === 'imperial'} label="MPH / YDS" onClick={() => setUnitSystem('imperial')} />
              <Tile selected={unitSystem === 'metric'} label="KMH / M" onClick={() => setUnitSystem('metric')} />
            </div>
          </>
        ) : null}

        {step === 'theme' ? (
          <>
            <h1 id="onboarding-title" className="onboarding__title">
              {t('onboarding.themeTitle')}
            </h1>
            <div className="onboarding__grid onboarding__grid--theme">
              <Tile
                selected={theme === 'dark'}
                appearance="dark"
                label={t('menu.themeDark')}
                preview={<ThemeSwatch appearance="dark" />}
                onClick={() => setTheme('dark')}
              />
              <Tile
                selected={theme === 'light'}
                appearance="light"
                label={t('menu.themeLight')}
                preview={<ThemeSwatch appearance="light" />}
                onClick={() => setTheme('light')}
              />
            </div>
          </>
        ) : null}

        {step === 'live' ? (
          <>
            <h1 id="onboarding-title" className="onboarding__title">
              {t('onboarding.liveViewTitle')}
            </h1>
            <div className="onboarding__grid onboarding__grid--live">
              {LIVE_MODES.map((option) => (
                <Tile
                  key={option.id}
                  selected={mode === option.id}
                  label={t(option.label)}
                  detail={t(option.detail)}
                  preview={<LiveViewDemo mode={option.id} />}
                  onClick={() => setMode(option.id)}
                  footer={
                    option.id === 'timed' && mode === 'timed' ? (
                      <div className="onboarding__durations" role="group" aria-label={t('onboarding.duration')}>
                        {LIVE_VIEW_DURATIONS_MS.map((ms) => (
                          <button
                            key={ms}
                            type="button"
                            className={`onboarding__chip${durationMs === ms ? ' onboarding__chip--selected' : ''}`}
                            aria-pressed={durationMs === ms}
                            onClick={() => handleDuration(String(ms))}
                          >
                            {t(DURATION_KEYS[ms])}
                          </button>
                        ))}
                      </div>
                    ) : null
                  }
                />
              ))}
            </div>
          </>
        ) : null}

        {step === 'done' ? (
          <div className="onboarding__welcome">
            <h1 id="onboarding-title" className="onboarding__title">
              {t('onboarding.doneTitle')}
            </h1>
            <p className="onboarding__detail">{t('onboarding.doneDetail')}</p>
          </div>
        ) : null}
      </div>

      {step !== 'welcome' ? (
        <div className="onboarding__chrome">
          <button type="button" className="onboarding__nav onboarding__nav--back" onClick={goBack}>
            {t('onboarding.back')}
          </button>
          <div className="onboarding__dots" aria-label={t('onboarding.step', { current, total: 5 })}>
            {STEPS.map((id, index) => (
              <span
                key={id}
                className={`onboarding__dot${index === stepIndex ? ' onboarding__dot--active' : ''}`}
                aria-current={index === stepIndex ? 'step' : undefined}
              />
            ))}
          </div>
          {step === 'done' ? (
            <button type="button" className="onboarding__nav onboarding__nav--primary" onClick={handleStart}>
              {t('onboarding.start')}
            </button>
          ) : (
            <button type="button" className="onboarding__nav onboarding__nav--primary" onClick={goNext}>
              {t('onboarding.continue')}
            </button>
          )}
        </div>
      ) : null}
    </div>
  );
}

import { useMemo, type ReactNode } from 'react';
import type { Shot } from '../../types/shot';
import { computeSwingSpeedStats, filterShotsByProfile } from '../../types/shot';
import { useUnitPreference } from '../../state/useUnitPreference';
import { useI18n } from '../../i18n/useI18n';
import { useSharedFitFontSize } from '../../hooks/useFitFontSize';
import { useLiveViewStore } from '../../stores/useLiveViewStore';
import { EstimatedMark, ExperimentalMark, MetricCard } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';
import { buildLiveMetrics, pinSelectedMetric } from './liveMetrics';
import { useShotSpotlight } from './useShotSpotlight';

interface LivePanelProps {
  shot: Shot | null;
  shots: Shot[];
  profileId: string;
  profileName: string;
  clubLabel: string;
  /** Undefined outside swing-speed mode. Scopes the swing stats to one implement. */
  activeTrainingImplement?: string;
  /** Metric pinned top-left while the full table remains visible. */
  selectedMetricId?: string | null;
  onSelectMetric?: (id: string) => void;
  /** True for a freshly captured shot (not a restored session). */
  isNewShot?: boolean;
  /** Camera ball-detection is running. */
  ballDetectionEnabled?: boolean;
  /** YOLO currently sees a ball. */
  ballDetected?: boolean;
  /** Pinned header control, e.g. Change club. */
  headerAction?: ReactNode;
}

/**
 * Ten-metric table. Tapping a tile selects it: the title turns accent yellow
 * and the tile moves to the top-left without hiding the remaining metrics.
 */
export function LivePanel({
  shot,
  shots,
  profileId,
  profileName,
  clubLabel,
  activeTrainingImplement,
  selectedMetricId = null,
  onSelectMetric,
  isNewShot = false,
  ballDetectionEnabled = false,
  ballDetected = false,
  headerAction,
}: LivePanelProps) {
  const { locale, t } = useI18n();
  const { unitSystem } = useUnitPreference();
  useLiveViewStore((state) => state.mode);
  useLiveViewStore((state) => state.durationMs);
  const { mode, durationMs } = useLiveViewStore.getState();
  const profileShots = useMemo(() => filterShotsByProfile(shots, profileId), [shots, profileId]);
  const displayedShot = profileShots[profileShots.length - 1] ?? null;
  const isProfileNewShot = Boolean(isNewShot && shot && displayedShot && shot.timestamp === displayedShot.timestamp);

  const swingStats = useMemo(
    () => computeSwingSpeedStats(profileShots, { profileId, trainingImplement: activeTrainingImplement }),
    [profileShots, profileId, activeTrainingImplement]
  );
  const metrics = useMemo(
    () =>
      displayedShot ? pinSelectedMetric(buildLiveMetrics(displayedShot, unitSystem, swingStats), selectedMetricId) : [],
    // locale is not read in the factory; t() is a stable import. Without it,
    // changing language would keep stale metric labels.
    // eslint-disable-next-line react-hooks/exhaustive-deps -- see above
    [displayedShot, unitSystem, swingStats, selectedMetricId, locale]
  );
  const selected = metrics[0] ?? null;
  const { open: spotlightOpen, dismiss } = useShotSpotlight(mode, durationMs, isProfileNewShot);
  const gridRef = useSharedFitFontSize(
    metrics.length > 0,
    metrics.map((metric) => `${metric.value}:${metric.unit ?? ''}`).join('|')
  );
  const showBallWarning = ballDetectionEnabled && !ballDetected;
  const ballWarning = showBallWarning ? (
    <div className="live-panel__ball-warning" role="alert">
      <span className="live-panel__ball-warning-title">{t('live.noBall')}</span>
      <span className="live-panel__ball-warning-detail">{t('live.noBallDetail')}</span>
    </div>
  ) : null;

  const header = <PanelHeader title={t('nav.live')} subtitle={profileName} club={clubLabel} actions={headerAction} />;

  if (!selected) {
    return (
      <div className="panel">
        {header}
        <div className="panel__body panel__body--empty live-panel__empty">
          {ballWarning}
          <span className="panel__empty-title live-panel__empty-title">{t('live.ready')}</span>
          <span className="panel__empty-detail live-panel__empty-detail">{t('live.readyDetail')}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="panel">
      {header}
      <div className="panel__body live-panel__body">
        {ballWarning}
        {isProfileNewShot ? <div className="shot-flash" /> : null}
        {spotlightOpen && selected ? (
          <button type="button" className="live-panel__spotlight" aria-label={t('live.hideOverlay')} onClick={dismiss}>
            <span className="live-panel__spotlight-label">
              {selected.label} · {clubLabel}
              {selected.estimated ? <EstimatedMark /> : null}
              {selected.experimental ? <ExperimentalMark /> : null}
            </span>
            <div className="live-panel__spotlight-value-row">
              <span className="live-panel__spotlight-value">{selected.value}</span>
              {selected.unit ? <span className="live-panel__spotlight-unit">{selected.unit}</span> : null}
            </div>
            {selected.subtext ? <span className="live-panel__spotlight-subtext">{selected.subtext}</span> : null}
          </button>
        ) : null}
        <div ref={gridRef} className={`live-panel__grid live-panel__grid--of-${metrics.length}`}>
          {metrics.map((metric) => (
            <MetricCard
              key={metric.id}
              label={metric.label}
              value={metric.value}
              unit={metric.unit}
              subtext={metric.subtext}
              estimated={metric.estimated}
              experimental={metric.experimental}
              confidence={metric.confidence}
              confidenceLabel={metric.confidenceLabel}
              labelPosition="above"
              selected={metric.id === selected.id}
              onClick={onSelectMetric ? () => onSelectMetric(metric.id) : undefined}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

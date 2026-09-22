import { useMemo, useRef, useState, type ReactNode } from 'react';
import type { Shot } from '../../types/shot';
import {
  computeStats,
  computeSwingSpeedStats,
  filterShotsByProfile,
  getUniqueClubs,
  isSwingSpeedShot,
} from '../../types/shot';
import { useDragScroll } from '../../hooks/useDragScroll';
import { useUnitPreference } from '../../state/useUnitPreference';
import { useI18n } from '../../i18n/useI18n';
import { formatDistance, formatSpeed, getDistanceUnit, getSpeedUnit } from '../../utils/units';
import { MetricCard } from '../ui/MetricCard';
import { PanelHeader } from './PanelHeader';

interface StatsPanelProps {
  shots: Shot[];
  activeClub: string;
  profileId: string;
  profileName: string;
  /** Pinned header control, e.g. Clear session. */
  headerAction?: ReactNode;
}

interface StatTile {
  id: string;
  label: string;
  value: string;
  unit?: string;
}

/**
 * Design doc 7a: session summary as a hairline tile grid, with the per-club
 * filter chips above the tiles. Six tiles for a ball-strike session (3x2 as
 * drawn), four for a swing-speed one.
 */
export function StatsPanel({ shots, activeClub, profileId, profileName, headerAction }: StatsPanelProps) {
  const { t } = useI18n();
  const profileShots = useMemo(() => filterShotsByProfile(shots, profileId), [shots, profileId]);
  const hasShotsForActiveClub = profileShots.some((shot) => shot.club === activeClub);
  const [selectedClub, setSelectedClub] = useState<string | null>(hasShotsForActiveClub ? activeClub : null);
  const [prevActiveClub, setPrevActiveClub] = useState(activeClub);
  const [prevProfileId, setPrevProfileId] = useState(profileId);
  const chipRef = useRef<HTMLDivElement>(null);
  const chipScroll = useDragScroll(chipRef, 'x');

  // Update state during render when the prop changes, rather than in an effect.
  if (activeClub !== prevActiveClub || profileId !== prevProfileId) {
    setPrevActiveClub(activeClub);
    setPrevProfileId(profileId);
    setSelectedClub(hasShotsForActiveClub ? activeClub : null);
  }

  const { unitSystem } = useUnitPreference();
  const speedUnit = getSpeedUnit(unitSystem);
  const distanceUnit = getDistanceUnit(unitSystem);

  const availableClubs = useMemo(() => getUniqueClubs(profileShots), [profileShots]);
  const clubCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const shot of profileShots) {
      counts[shot.club] = (counts[shot.club] ?? 0) + 1;
    }
    return counts;
  }, [profileShots]);

  const filteredShots = useMemo(
    () => (selectedClub === null ? profileShots : profileShots.filter((shot) => shot.club === selectedClub)),
    [profileShots, selectedClub]
  );

  const stats = useMemo(() => computeStats(filteredShots), [filteredShots]);
  const swingStats = useMemo(() => computeSwingSpeedStats(filteredShots), [filteredShots]);
  const isSwingSpeedSession = filteredShots.length > 0 && filteredShots.every(isSwingSpeedShot);

  const tiles: StatTile[] = useMemo(() => {
    if (isSwingSpeedSession) {
      return [
        { id: 'count', label: t('metric.swings'), value: String(swingStats.count) },
        {
          id: 'last',
          label: t('stats.last'),
          value: formatSpeed(swingStats.last_speed_mph, unitSystem, 1),
          unit: speedUnit,
        },
        {
          id: 'best',
          label: t('metric.best'),
          value: formatSpeed(swingStats.best_speed_mph, unitSystem, 1),
          unit: speedUnit,
        },
        {
          id: 'avg',
          label: t('metric.average'),
          value: formatSpeed(swingStats.avg_speed_mph, unitSystem, 1),
          unit: speedUnit,
        },
      ];
    }

    return [
      { id: 'count', label: t('stats.shots'), value: String(stats.shot_count) },
      {
        id: 'avg_ball',
        label: t('stats.avgBall'),
        value: formatSpeed(stats.avg_ball_speed, unitSystem, 1),
        unit: speedUnit,
      },
      {
        id: 'max_ball',
        label: t('stats.maxBall'),
        value: formatSpeed(stats.max_ball_speed, unitSystem, 1),
        unit: speedUnit,
      },
      {
        id: 'avg_carry',
        label: t('stats.avgCarry'),
        value: formatDistance(stats.avg_carry_est, unitSystem, 0),
        unit: distanceUnit,
      },
      {
        id: 'avg_club',
        label: t('stats.avgClub'),
        value: stats.avg_club_speed === null ? '—' : formatSpeed(stats.avg_club_speed, unitSystem, 1),
        unit: stats.avg_club_speed === null ? undefined : speedUnit,
      },
      {
        id: 'avg_smash',
        label: t('stats.avgSmash'),
        value: stats.avg_smash_factor === null ? '—' : stats.avg_smash_factor.toFixed(2),
      },
    ];
  }, [isSwingSpeedSession, stats, swingStats, unitSystem, speedUnit, distanceUnit, t]);

  const clubFilters = (
    <div className="stats-panel__chips" role="group" aria-label={t('stats.filterByClub')}>
      <button
        type="button"
        className={`panel-chip${selectedClub === null ? ' panel-chip--active' : ''}`}
        aria-pressed={selectedClub === null}
        onClick={() => setSelectedClub(null)}
      >
        {t('stats.all', { count: profileShots.length })}
      </button>
      <div
        className="panel-chips stats-panel__chip-scroll"
        ref={chipRef}
        onPointerDown={chipScroll.onPointerDown}
        onPointerMove={chipScroll.onPointerMove}
        onPointerUp={chipScroll.onPointerUp}
        onPointerCancel={chipScroll.onPointerCancel}
        onClickCapture={chipScroll.onClickCapture}
      >
        {availableClubs.map((club) => (
          <button
            key={club}
            type="button"
            className={`panel-chip${selectedClub === club ? ' panel-chip--active' : ''}`}
            aria-pressed={selectedClub === club}
            onClick={() => setSelectedClub(club)}
          >
            {club.toUpperCase()} ({clubCounts[club] ?? 0})
          </button>
        ))}
      </div>
    </div>
  );

  return (
    <div className="panel">
      <PanelHeader title={t('nav.stats')} subtitle={profileName} actions={headerAction} />
      <div className="panel__body stats-panel">
        {profileShots.length > 0 ? clubFilters : null}
        {profileShots.length === 0 ? (
          <div className="panel__body--empty">
            <span className="panel__empty-title">{t('stats.noShots')}</span>
            <span className="panel__empty-detail">{t('stats.noShotsDetail')}</span>
          </div>
        ) : (
          <div className={`stats-panel__grid stats-panel__grid--of-${tiles.length}`}>
            {tiles.map((tile) => (
              <MetricCard key={tile.id} label={tile.label} value={tile.value} unit={tile.unit} labelPosition="above" />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

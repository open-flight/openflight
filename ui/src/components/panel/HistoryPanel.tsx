import { useEffect, useRef, useState } from 'react';
import { useDragScroll } from '../../hooks/useDragScroll';
import { getServerOrigin } from '../../utils/serverOrigin';
import { PanelHeader } from './PanelHeader';

interface Session {
  id: string;
  filename: string;
  started_at: string;
  location: string;
  shot_count: number;
  camera_capture_count: number;
  hidden: boolean;
}

interface Shot {
  shot_number: number;
  timestamp: string | null;
  club: string | null;
  ball_speed_mph: number | null;
  club_speed_mph: number | null;
  estimated_carry_yards: number | null;
  smash_factor: number | null;
  spin_rpm: number | null;
  camera_available: boolean;
  recorded_data: Record<string, unknown>;
}

interface Details {
  id: string;
  started_at: string | null;
  shot_count: number;
  shots: Shot[];
}

interface Props {
  onReplayShot: (sessionId: string, shotNumber: number) => void;
}

function displayDate(value: string | null | undefined): string {
  if (!value) return 'Unknown date';

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;

  return date.toLocaleDateString('en-IE', {
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  });
}

function displayTime(value: string | null | undefined): string {
  if (!value) return '';

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '';

  return date.toLocaleTimeString('en-IE', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
}

function metric(value: number | null, decimals = 1): string {
  return typeof value === 'number' && Number.isFinite(value) ? value.toFixed(decimals) : '—';
}

function ShotDetails({ data }: { data: Record<string, unknown> }) {
  return (
    <div className="history-extra-data">
      {Object.entries(data)
        .filter(([key, value]) => value !== null && key !== 'camera_replay')
        .map(([key, value]) => (
          <div key={key}>
            <span>{key.replaceAll('_', ' ')}</span>
            <strong>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</strong>
          </div>
        ))}
    </div>
  );
}

export function HistoryPanel({ onReplayShot }: Props) {
  const [showEmpty, setShowEmpty] = useState(false);
  const [showHidden, setShowHidden] = useState(false);
  const [shortOnly, setShortOnly] = useState(false);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [details, setDetails] = useState<Details | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailsLoading, setDetailsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const dragScroll = useDragScroll(scrollRef);

  // The list and a session's shots share one scroller; start each at the top.
  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = 0;
  }, [selectedId]);

  useEffect(() => {
    const controller = new AbortController();
    void fetch(`${getServerOrigin()}/api/sessions/history?include_empty=${showEmpty}&show_hidden=${showHidden}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error('Unable to load sessions');
        return response.json() as Promise<{ sessions: Session[] }>;
      })
      .then((data) => {
        if (!controller.signal.aborted) setSessions(data.sessions);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError('Unable to load session history.');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });

    return () => controller.abort();
  }, [showEmpty, showHidden]);

  useEffect(() => {
    if (!selectedId) {
      return;
    }

    const controller = new AbortController();
    void fetch(`${getServerOrigin()}/api/sessions/history/${encodeURIComponent(selectedId)}`, {
      signal: controller.signal,
    })
      .then(async (response) => {
        if (!response.ok) throw new Error('Unable to load shots');
        return response.json() as Promise<Details>;
      })
      .then((data) => {
        if (!controller.signal.aborted) setDetails(data);
      })
      .catch(() => {
        if (!controller.signal.aborted) {
          setError('Unable to load recorded shots.');
        }
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailsLoading(false);
      });

    return () => controller.abort();
  }, [selectedId]);

  async function changeVisibility(id: string, hidden: boolean) {
    try {
      const response = await fetch(`${getServerOrigin()}/api/sessions/history/${encodeURIComponent(id)}/visibility`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ hidden }),
      });
      if (!response.ok) throw new Error('Visibility update failed');

      setSessions((items) =>
        items.map((item) => (item.id === id ? { ...item, hidden } : item)).filter((item) => showHidden || !item.hidden)
      );
      setError(null);
    } catch {
      setError('Unable to change session visibility.');
    }
  }

  const selected = sessions.find((session) => session.id === selectedId);

  return (
    <div className="panel">
      <PanelHeader
        title="History"
        subtitle={selectedId ? 'Recorded shot measurements' : `${sessions.length} previous sessions`}
      />

      <div
        className="panel__body history-content"
        role="region"
        aria-label={selectedId ? 'Recorded shots in session' : 'Saved sessions'}
        ref={scrollRef}
        onPointerDown={dragScroll.onPointerDown}
        onPointerMove={dragScroll.onPointerMove}
        onPointerUp={dragScroll.onPointerUp}
        onPointerCancel={dragScroll.onPointerCancel}
        onClickCapture={dragScroll.onClickCapture}
      >
        {selectedId ? (
          <>
            <button
              className="history-back"
              type="button"
              onClick={() => {
                setSelectedId(null);
                setDetails(null);
                setDetailsLoading(false);
                setError(null);
              }}
            >
              <span aria-hidden="true">‹</span> All sessions
            </button>

            <div className="history-session-heading">
              <div>
                <div className="history-eyebrow">PRACTICE SESSION</div>
                <h2>{displayDate(selected?.started_at ?? details?.started_at)}</h2>
                <p>
                  {displayTime(selected?.started_at ?? details?.started_at)}
                  {' · '}
                  {selected?.location ?? 'Range'}
                </p>
              </div>
              <span className="history-count">{details?.shot_count ?? selected?.shot_count ?? 0} shots</span>
            </div>

            {detailsLoading && <p className="history-message">Loading shots…</p>}

            {details?.shots.map((shot, index) => (
              <div className="history-shot-row" key={`${shot.shot_number}-${index}`}>
                <div className="history-shot-heading">
                  <div>
                    <span className="history-shot-number">SHOT {shot.shot_number}</span>
                    <strong>{shot.club || 'Unknown club'}</strong>
                  </div>
                  {shot.camera_available ? (
                    <button
                      type="button"
                      className="history-replay"
                      onClick={() => onReplayShot(selectedId, shot.shot_number)}
                    >
                      ▶ Replay video
                    </button>
                  ) : (
                    <span className="history-no-video">No video</span>
                  )}
                </div>

                <div className="history-metrics">
                  <div>
                    <span>BALL SPEED</span>
                    <strong>
                      {metric(shot.ball_speed_mph)} <small>mph</small>
                    </strong>
                  </div>
                  <div>
                    <span>CLUB SPEED</span>
                    <strong>
                      {metric(shot.club_speed_mph)} <small>mph</small>
                    </strong>
                  </div>
                  <div>
                    <span>CARRY</span>
                    <strong>
                      {metric(shot.estimated_carry_yards)} <small>yd</small>
                    </strong>
                  </div>
                  <div>
                    <span>SMASH</span>
                    <strong>{metric(shot.smash_factor, 2)}</strong>
                  </div>
                </div>
                <details className="history-detail-section">
                  <summary>Golf Performance Data</summary>
                  <div className="history-golf-data">
                    {[
                      ['Launch angle', 'launch_angle_vertical', '°'],
                      ['Launch direction', 'launch_angle_horizontal', '°'],
                      ['Spin rate', 'spin_rpm', 'rpm'],
                      ['Spin-adjusted carry', 'carry_spin_adjusted', 'yd'],
                      ['Club path', 'club_path_deg', '°'],
                      ['Spin axis', 'spin_axis_deg', '°'],
                      ['Attack angle', 'experimental_attack_angle_deg', '°'],
                      ['Estimated club path', 'experimental_club_path_deg', '°'],
                      ['Spin confidence', 'spin_confidence', ''],
                      ['Launch confidence', 'launch_angle_vertical_confidence', ''],
                    ].map(([label, key, unit]) => {
                      const value = shot.recorded_data[key];
                      return (
                        <div key={key}>
                          <span>{label}</span>
                          <strong>
                            {typeof value === 'number'
                              ? key.endsWith('confidence')
                                ? (value * 100).toFixed(1) + '%'
                                : value.toFixed(1) + ' ' + unit
                              : '—'}
                          </strong>
                          {key === 'spin_rpm' && (
                            <small>{String(shot.recorded_data.spin_quality ?? 'Quality unknown')}</small>
                          )}
                          {key === 'launch_angle_vertical' && (
                            <small>{String(shot.recorded_data.launch_angle_vertical_source ?? 'Source unknown')}</small>
                          )}
                          {key === 'experimental_attack_angle_deg' && <small>Experimental</small>}
                          {key === 'experimental_club_path_deg' && <small>Experimental</small>}
                        </div>
                      );
                    })}
                    <div>
                      <span>Estimated carry range</span>
                      <strong>
                        {Array.isArray(shot.recorded_data.carry_range)
                          ? shot.recorded_data.carry_range
                              .map((value) => (typeof value === 'number' ? value.toFixed(1) : '—'))
                              .join('–') + ' yd'
                          : '—'}
                      </strong>
                    </div>
                    <div>
                      <span>Spin quality</span>
                      <strong>{String(shot.recorded_data.spin_quality ?? 'Unavailable')}</strong>
                    </div>
                    <div>
                      <span>Launch source</span>
                      <strong>{String(shot.recorded_data.launch_angle_vertical_source ?? 'Unavailable')}</strong>
                    </div>
                  </div>
                </details>
                <details className="history-detail-section">
                  <summary>Technical &amp; Raw Data</summary>
                  <ShotDetails data={shot.recorded_data} />
                </details>
              </div>
            ))}

            {details && details.shots.length === 0 && (
              <p className="history-message">No recorded shots in this session.</p>
            )}
          </>
        ) : (
          <>
            <div className="history-toolbar">
              <span className="history-eyebrow">SAVED SESSIONS</span>
              <label className="history-toggle">
                <input type="checkbox" checked={shortOnly} onChange={(event) => setShortOnly(event.target.checked)} />
                Short sessions (1–4)
              </label>
              <label className="history-toggle">
                <input
                  type="checkbox"
                  checked={showHidden}
                  onChange={(event) => {
                    setLoading(true);
                    setError(null);
                    setShowHidden(event.target.checked);
                  }}
                />
                Show hidden
              </label>
              <label className="history-toggle">
                <input
                  type="checkbox"
                  checked={showEmpty}
                  onChange={(event) => {
                    setLoading(true);
                    setError(null);
                    setShowEmpty(event.target.checked);
                  }}
                />
                Show empty
              </label>
            </div>

            {loading && <p className="history-message">Loading sessions…</p>}

            {sessions
              .filter((session) => !shortOnly || (session.shot_count >= 1 && session.shot_count <= 4))
              .map((session) => (
                <div className="history-session-item" key={session.id}>
                  <button
                    className="history-session-row"
                    type="button"
                    key={session.id}
                    onClick={() => {
                      setDetails(null);
                      setDetailsLoading(true);
                      setError(null);
                      setSelectedId(session.id);
                    }}
                  >
                    <div className="history-session-top">
                      <div>
                        <strong>{displayDate(session.started_at)}</strong>
                        <span>
                          {displayTime(session.started_at)}
                          {' · '}
                          {session.location}
                        </span>
                      </div>
                      <span className="history-chevron">›</span>
                    </div>
                    <div className="history-session-stats">
                      <span className="history-shots-count">{session.shot_count} shots</span>
                      <span>◉ {session.camera_capture_count} recordings</span>
                    </div>
                  </button>
                  <button
                    type="button"
                    className="history-visibility"
                    onClick={() => void changeVisibility(session.id, !session.hidden)}
                  >
                    {session.hidden ? 'Restore' : 'Hide'}
                  </button>
                </div>
              ))}

            {!loading && sessions.length === 0 && !error && <p className="history-message">No sessions found.</p>}
          </>
        )}

        {error && (
          <p className="history-error" role="alert">
            {error}
          </p>
        )}
      </div>
    </div>
  );
}

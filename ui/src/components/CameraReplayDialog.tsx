import { useRef, useState, type CSSProperties, type SyntheticEvent } from 'react';
import type { CameraReplay } from '../types/shot';
import { useI18n } from '../i18n/useI18n';
import { ProgressIndicator } from './ProgressIndicator';
import './CameraReplayDialog.css';

export type CameraReplayDialogState =
  | { kind: 'preparing' }
  | { kind: 'ready'; videoUrl: string }
  | { kind: 'error'; stage?: 'preparation' | 'playback'; message?: string };

interface CameraReplayDialogProps {
  replay: CameraReplay;
  state: CameraReplayDialogState;
  onClose: () => void;
  onRetry: () => void;
  onPlaybackError?: () => void;
}

const PLAYBACK_RATES = [1, 0.5, 0.25, 0.1] as const;

function formatTime(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return '0:00.0';
  const minutes = Math.floor(seconds / 60);
  const remainder = (seconds % 60).toFixed(1).padStart(4, '0');
  return `${minutes}:${remainder}`;
}

export function CameraReplayDialog({ replay, state, onClose, onRetry, onPlaybackError }: CameraReplayDialogProps) {
  const { t } = useI18n();
  const videoRef = useRef<HTMLVideoElement>(null);
  const [currentTime, setCurrentTime] = useState(0);
  const [duration, setDuration] = useState(replay.duration_seconds);
  const [paused, setPaused] = useState(true);
  const [playbackRate, setPlaybackRate] = useState<(typeof PLAYBACK_RATES)[number]>(1);
  const [loopEnabled, setLoopEnabled] = useState(false);
  const impactPosition = replay.frame_count > 1 ? (replay.trigger_frame / (replay.frame_count - 1)) * 100 : 0;
  const timelineStyle = { '--replay-impact-position': `${impactPosition}%` } as CSSProperties;

  const togglePlayback = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) {
      void video.play().catch(() => onPlaybackError?.());
    } else {
      video.pause();
    }
  };

  const restart = () => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = 0;
    void video.play().catch(() => onPlaybackError?.());
  };

  const selectPlaybackRate = (rate: (typeof PLAYBACK_RATES)[number]) => {
    setPlaybackRate(rate);
    if (videoRef.current) videoRef.current.playbackRate = rate;
  };

  const updateDuration = (event: SyntheticEvent<HTMLVideoElement>) => {
    event.currentTarget.playbackRate = playbackRate;
    const videoDuration = event.currentTarget.duration;
    if (Number.isFinite(videoDuration) && videoDuration > 0) {
      setDuration(videoDuration);
    }
  };

  return (
    <div className="camera-replay" role="presentation">
      <section className="camera-replay__dialog" role="dialog" aria-modal="true" aria-label={t('replay.title')}>
        {state.kind === 'preparing' ? (
          <div className="camera-replay__status">
            <ProgressIndicator variant="dialog" title={t('replay.preparing')} detail={t('replay.preparingDetail')} />
            <button type="button" className="camera-replay__button" onClick={onClose}>
              {t('replay.close')}
            </button>
          </div>
        ) : null}

        {state.kind === 'error' ? (
          <div className="camera-replay__status">
            <h2>{state.stage === 'playback' ? t('replay.playbackError') : t('replay.error')}</h2>
            <p>
              {state.message ??
                (state.stage === 'playback' ? t('replay.playbackErrorDetail') : t('replay.errorDetail'))}
            </p>
            <div className="camera-replay__status-actions">
              <button type="button" className="camera-replay__button camera-replay__button--primary" onClick={onRetry}>
                {t('replay.tryAgain')}
              </button>
              <button type="button" className="camera-replay__button" onClick={onClose}>
                {t('replay.close')}
              </button>
            </div>
          </div>
        ) : null}

        {state.kind === 'ready' ? (
          <>
            <header className="camera-replay__header">
              <div>
                <span className="camera-replay__eyebrow">{t('replay.slowMotion')}</span>
                <h2>{t('replay.title')}</h2>
              </div>
              <button type="button" className="camera-replay__button" onClick={onClose}>
                {t('replay.close')}
              </button>
            </header>
            <div className="camera-replay__body">
              <div className="camera-replay__stage">
                <div className="camera-replay__viewport">
                  <video
                    ref={videoRef}
                    src={state.videoUrl}
                    className={`camera-replay__video ${replay.display_mirror_horizontal ? 'camera-replay__video--mirrored' : ''}`}
                    aria-label={t('replay.video')}
                    autoPlay
                    loop={loopEnabled}
                    muted
                    playsInline
                    preload="auto"
                    onClick={togglePlayback}
                    onLoadedMetadata={updateDuration}
                    onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
                    onPlay={() => setPaused(false)}
                    onPause={() => setPaused(true)}
                    onEnded={() => setPaused(true)}
                    onError={() => onPlaybackError?.()}
                  />
                </div>
              </div>
              <div className="camera-replay__controls">
                <div className="camera-replay__timeline" style={timelineStyle}>
                  <input
                    className="camera-replay__scrubber"
                    type="range"
                    min="0"
                    max={Math.max(duration, 0.01)}
                    step="0.01"
                    value={Math.min(currentTime, duration)}
                    aria-label={t('replay.scrub')}
                    onChange={(event) => {
                      const nextTime = Number(event.target.value);
                      setCurrentTime(nextTime);
                      if (videoRef.current) videoRef.current.currentTime = nextTime;
                    }}
                  />
                  <span className="camera-replay__impact" aria-label={t('replay.impact')} />
                </div>
                <div className="camera-replay__playback-row">
                  <span className="camera-replay__playback-label">{t('replay.speed')}</span>
                  <div className="camera-replay__rate-options" role="group" aria-label={t('replay.speed')}>
                    {PLAYBACK_RATES.map((rate) => (
                      <button
                        key={rate}
                        type="button"
                        className={`camera-replay__button camera-replay__rate ${playbackRate === rate ? 'camera-replay__button--active' : ''}`}
                        aria-pressed={playbackRate === rate}
                        onClick={() => selectPlaybackRate(rate)}
                      >
                        {`${rate}×`}
                      </button>
                    ))}
                  </div>
                  <button
                    type="button"
                    className={`camera-replay__button camera-replay__loop ${loopEnabled ? 'camera-replay__button--active' : ''}`}
                    aria-pressed={loopEnabled}
                    onClick={() => setLoopEnabled((enabled) => !enabled)}
                  >
                    {t('replay.loop')}
                  </button>
                </div>
                <div className="camera-replay__control-row">
                  <button
                    type="button"
                    className="camera-replay__button camera-replay__button--primary"
                    onClick={togglePlayback}
                  >
                    {paused ? t('replay.play') : t('replay.pause')}
                  </button>
                  <button type="button" className="camera-replay__button" onClick={restart}>
                    {t('replay.restart')}
                  </button>
                  <span className="camera-replay__time">
                    {formatTime(currentTime)} / {formatTime(duration)}
                  </span>
                </div>
              </div>
            </div>
          </>
        ) : null}
      </section>
    </div>
  );
}

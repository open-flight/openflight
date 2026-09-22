import { useEffect, useState, type CSSProperties } from 'react';
import { useCameraPointerDragScroll } from '../hooks/useCameraPointerDragScroll';
import type { CameraAutoExposureStatus, CameraCaptureSettings } from '../stores/useCameraStore';
import { verticalViewTargets } from '../utils/cameraView';
import { getServerOrigin } from '../utils/serverOrigin';
import './CameraFeed.css';

interface CameraFeedProps {
  captureSettings: CameraCaptureSettings;
  captureSettingsError: string | null;
  onUpdateCaptureSettings: (settings: Partial<CameraCaptureSettings>) => void;
}

interface CaptureSettingsPanelProps {
  settings: CameraCaptureSettings;
  exposureQuality: ExposureQuality | null;
  error: string | null;
  onUpdate: (settings: Partial<CameraCaptureSettings>) => void;
}

const PREVIEW_URL = `${getServerOrigin()}/api/camera/preview.jpg`;
const EXPOSURE_QUALITY_URL = `${getServerOrigin()}/api/camera/exposure-quality`;
const PREVIEW_REFRESH_MS = 5000;
const BALL_GUIDE_X_PCT = 50;
const BALL_GUIDE_Y_PCT = 78;

type PreviewState = 'checking' | 'available' | 'unavailable';

interface ExposureQuality {
  sample_available: boolean;
  status: 'good' | 'too_dark' | 'too_bright' | 'marginal' | 'unavailable';
  recommendation?: 'brighter' | 'darker' | 'hold';
  message?: string;
  clipped_pct?: number;
  contrast?: number;
  auto_exposure?: CameraAutoExposureStatus;
}

const AUTO_EXPOSURE_LABELS: Record<CameraAutoExposureStatus['status'], string> = {
  ready: 'Ready',
  adjusting: 'Adjusting',
  lighting_required: 'Lighting needed',
  unavailable: 'Waiting',
};

function CaptureSettingsPanel({ settings, exposureQuality, error, onUpdate }: CaptureSettingsPanelProps) {
  const verticalOffset = settings.vertical_offset_px ?? 0;
  const verticalMin = settings.vertical_offset_min_px ?? verticalOffset;
  const verticalMax = settings.vertical_offset_max_px ?? verticalOffset;
  const verticalStep = settings.vertical_offset_step_px ?? 10;
  const viewTargets = verticalViewTargets(verticalOffset, verticalStep, settings.rotate_180 ?? false);
  const viewUpOffset = viewTargets.up;
  const viewDownOffset = viewTargets.down;
  const autoExposure = exposureQuality?.auto_exposure ?? settings.auto_exposure;
  const exposureObservation = autoExposure?.observation;
  const exposureStatus = autoExposure?.capture_deferred
    ? 'Capturing shot'
    : AUTO_EXPOSURE_LABELS[autoExposure?.status ?? 'unavailable'];

  return (
    <aside className="camera-settings">
      <div className="camera-settings__heading">
        <div>
          <span className="camera-settings__eyebrow">Capture controls</span>
          <h3>Camera setup</h3>
        </div>
        <span className={`camera-settings__armed ${settings.armed ? 'camera-settings__armed--ready' : ''}`}>
          {settings.armed ? 'Armed' : settings.running ? 'Filling' : 'Offline'}
        </span>
      </div>

      <div className="camera-settings__controls">
        <section className="camera-settings__section">
          <div className="camera-settings__section-title">
            <span>Sensor view</span>
            <small>real 320 × 200 crop</small>
          </div>
          <div className="camera-settings__view-controls">
            <button
              type="button"
              disabled={!settings.raw_crop_adjustable || viewUpOffset < verticalMin || viewUpOffset > verticalMax}
              onClick={() => onUpdate({ vertical_offset_px: viewUpOffset })}
            >
              View up
            </button>
            <output>{`${verticalOffset > 0 ? '+' : ''}${verticalOffset} px`}</output>
            <button
              type="button"
              disabled={!settings.raw_crop_adjustable || viewDownOffset < verticalMin || viewDownOffset > verticalMax}
              onClick={() => onUpdate({ vertical_offset_px: viewDownOffset })}
            >
              View down
            </button>
          </div>
          <p className="camera-settings__note">
            Moves the captured sensor window by 10 pixels and briefly rearms the rolling buffer.
          </p>
        </section>

        <section className="camera-settings__section">
          <div className="camera-settings__section-title">
            <span>Automatic exposure</span>
            <small>calibrates once at startup</small>
          </div>
          <div
            className={`camera-settings__auto-status camera-settings__auto-status--${autoExposure?.status ?? 'unavailable'}`}
          >
            <div>
              <strong>{exposureStatus}</strong>
              <span>{autoExposure?.message ?? 'Waiting for the first camera check'}</span>
            </div>
            <span className="camera-settings__auto-dot" aria-hidden="true" />
          </div>
          <dl className="camera-settings__exposure-metrics">
            <div>
              <dt>Shutter</dt>
              <dd>{autoExposure?.exposure_us ?? settings.exposure_us ?? '—'} µs</dd>
            </div>
            <div>
              <dt>Gain</dt>
              <dd>{autoExposure?.gain ?? settings.gain ?? '—'}×</dd>
            </div>
            <div>
              <dt>Impact median</dt>
              <dd>{exposureObservation?.median ?? '—'}</dd>
            </div>
            <div>
              <dt>Contrast</dt>
              <dd>{exposureObservation?.contrast ?? exposureQuality?.contrast ?? '—'}</dd>
            </div>
          </dl>
          <div
            className={`camera-settings__analysis-state ${autoExposure?.analysis_eligible ? 'camera-settings__analysis-state--ready' : ''}`}
          >
            <strong>{autoExposure?.analysis_eligible ? 'Camera analysis active' : 'Radar fallback active'}</strong>
            <span>
              {autoExposure?.analysis_eligible
                ? 'Camera-assisted aim, club path, and attack angle are eligible.'
                : 'Preview and raw clips continue recording; camera-derived metrics are withheld.'}
            </span>
          </div>
          <p className="camera-settings__note">
            Motion blur risk: <strong>{autoExposure?.motion_blur_risk ?? 'checking'}</strong>. Add or redirect light if
            the camera reaches its limit.
          </p>
        </section>

        <section className="camera-settings__section">
          <div className="camera-settings__section-title">
            <span>Ball placement guide</span>
            <small>fixed recommendation</small>
          </div>
          <div className="camera-settings__ball-guide-position">50% across · 78% down</div>
          <p className="camera-settings__note">
            Adjust the physical setup or sensor view until the center of the ball sits on the +. This leaves room above
            the ball for club and launch tracking.
          </p>
        </section>

        <section className="camera-settings__section camera-settings__section--summary">
          <div className="camera-settings__section-title">
            <span>Capture</span>
            <small>restart required to change</small>
          </div>
          <dl className="camera-settings__summary">
            <div>
              <dt>Mode</dt>
              <dd>{settings.width && settings.height ? `${settings.width} × ${settings.height}` : '—'}</dd>
            </div>
            <div>
              <dt>Requested</dt>
              <dd>{settings.fps ? `${settings.fps.toFixed(0)} fps` : '—'}</dd>
            </div>
            <div>
              <dt>Buffer</dt>
              <dd>
                {settings.pre_ms !== undefined && settings.post_ms !== undefined
                  ? `${settings.pre_ms.toFixed(0)} / ${settings.post_ms.toFixed(0)} ms`
                  : '—'}
              </dd>
            </div>
            <div>
              <dt>Frames</dt>
              <dd>
                {settings.pre_frames !== undefined && settings.post_frames !== undefined
                  ? `${settings.pre_frames} + ${settings.post_frames}`
                  : '—'}
              </dd>
            </div>
          </dl>
        </section>

        {error && <p className="camera-settings__error">{error}</p>}
      </div>
    </aside>
  );
}

/**
 * Camera tab.
 *
 * When the high-speed capture runtime is active (--camera-capture), shows a
 * still refreshed every 5 s from the concurrent preview stream. The raw
 * rolling buffer keeps running, so shots are never missed while viewing.
 */
export function CameraFeed({ captureSettings, captureSettingsError, onUpdateCaptureSettings }: CameraFeedProps) {
  useCameraPointerDragScroll();
  const [previewState, setPreviewState] = useState<PreviewState>('checking');
  const [previewSrc, setPreviewSrc] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [exposureQuality, setExposureQuality] = useState<ExposureQuality | null>(null);
  const crosshairStyle = {
    '--camera-crosshair-x': `${BALL_GUIDE_X_PCT}%`,
    '--camera-crosshair-y': `${BALL_GUIDE_Y_PCT}%`,
  } as CSSProperties;

  useEffect(() => {
    let cancelled = false;
    let objectUrl: string | null = null;
    let timer: ReturnType<typeof setInterval> | null = null;

    const refresh = async () => {
      try {
        const response = await fetch(`${PREVIEW_URL}?t=${Date.now()}`, { cache: 'no-store' });
        if (cancelled) return;
        if (response.status === 404) {
          setPreviewState('unavailable');
          if (timer) clearInterval(timer);
          return;
        }
        if (!response.ok) return;
        const blob = await response.blob();
        if (cancelled) return;
        const nextUrl = URL.createObjectURL(blob);
        if (objectUrl) URL.revokeObjectURL(objectUrl);
        objectUrl = nextUrl;
        setPreviewSrc(nextUrl);
        setLastUpdated(new Date());
        setPreviewState('available');
        const qualityResponse = await fetch(`${EXPOSURE_QUALITY_URL}?t=${Date.now()}`, { cache: 'no-store' });
        if (qualityResponse.ok && !cancelled) {
          setExposureQuality(await qualityResponse.json());
        }
      } catch {
        // Keep the last frame and retry after a transient network failure.
      }
    };

    refresh();
    timer = setInterval(refresh, PREVIEW_REFRESH_MS);
    return () => {
      cancelled = true;
      if (timer) clearInterval(timer);
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, []);

  if (previewState === 'available' || previewState === 'checking') {
    return (
      <div className="camera-feed camera-feed--capture">
        <div className="camera-feed__header">
          <div>
            <span className="camera-feed__eyebrow">High-speed capture</span>
            <h2 className="camera-feed__title">Camera alignment</h2>
          </div>
          <div className="camera-feed__header-status">
            <span
              className={`camera-feed__exposure-quality camera-feed__exposure-quality--${exposureQuality?.status ?? 'checking'}`}
              title={exposureQuality?.message ?? 'Analyzing the impact area'}
            >
              Auto exposure:{' '}
              {exposureQuality?.auto_exposure ? AUTO_EXPOSURE_LABELS[exposureQuality.auto_exposure.status] : 'checking'}
            </span>
            {lastUpdated && <span className="camera-feed__timestamp">preview {lastUpdated.toLocaleTimeString()}</span>}
          </div>
        </div>
        <div className="camera-feed__workspace">
          <div className="camera-feed__preview-column">
            {previewSrc ? (
              <div className="camera-feed__stream" style={crosshairStyle}>
                <img src={previewSrc} alt="Camera preview" className="camera-feed__video" />
                <svg
                  className="camera-feed__crosshair"
                  viewBox="0 0 100 100"
                  preserveAspectRatio="none"
                  aria-hidden="true"
                >
                  <line
                    x1={BALL_GUIDE_X_PCT}
                    y1="0"
                    x2={BALL_GUIDE_X_PCT}
                    y2="100"
                    vectorEffect="non-scaling-stroke"
                    className="camera-feed__hairline"
                  />
                  <line
                    x1="0"
                    y1={BALL_GUIDE_Y_PCT}
                    x2="100"
                    y2={BALL_GUIDE_Y_PCT}
                    vectorEffect="non-scaling-stroke"
                    className="camera-feed__hairline"
                  />
                </svg>
                <div className="camera-feed__center-cross" aria-hidden="true" />
                <div className="camera-feed__overlay">
                  <div className="camera-feed__status">Rolling buffer remains armed</div>
                </div>
              </div>
            ) : (
              <div className="camera-feed__message camera-feed__message--preview">
                <h3>Waiting for camera</h3>
                <p>Fetching the first preview from the capture runtime</p>
              </div>
            )}
          </div>
          <CaptureSettingsPanel
            settings={captureSettings}
            exposureQuality={exposureQuality}
            error={captureSettingsError}
            onUpdate={onUpdateCaptureSettings}
          />
        </div>
      </div>
    );
  }

  return (
    <div className="camera-feed camera-feed--unavailable">
      <div className="camera-feed__message">
        <h3>Camera Not Available</h3>
        <p>Start the server with --camera-capture to enable previews.</p>
      </div>
    </div>
  );
}

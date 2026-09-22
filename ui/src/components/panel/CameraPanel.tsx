import type { CameraCaptureSettings } from '../../stores/useCameraStore';
import { CameraFeed } from '../CameraFeed';

interface CameraPanelProps {
  captureSettings: CameraCaptureSettings;
  captureSettingsError: string | null;
  onUpdateCaptureSettings: (settings: Partial<CameraCaptureSettings>) => void;
}

/** High-speed capture setup and preview panel. */
export function CameraPanel({ captureSettings, captureSettingsError, onUpdateCaptureSettings }: CameraPanelProps) {
  return (
    <div className="panel camera-panel camera-panel--capture">
      <div className="panel__body camera-panel__body camera-panel__body--capture">
        <CameraFeed
          captureSettings={captureSettings}
          captureSettingsError={captureSettingsError}
          onUpdateCaptureSettings={onUpdateCaptureSettings}
        />
      </div>
    </div>
  );
}

import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CameraPanel } from './CameraPanel';

describe('CameraPanel', () => {
  it('renders the high-speed capture workspace', () => {
    const html = renderToString(
      <CameraPanel
        captureSettings={{ available: true, running: true, armed: true, width: 320, height: 200, fps: 600 }}
        captureSettingsError={null}
        onUpdateCaptureSettings={() => {}}
      />
    );

    expect(html).toContain('camera-panel--capture');
    expect(html).toContain('Camera setup');
    expect(html).toContain('rolling buffer');
  });
});

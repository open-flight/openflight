import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { CameraReplayDialog } from './CameraReplayDialog';

const replay = {
  id: 'replay-123',
  frame_count: 99,
  trigger_frame: 73,
  playback_fps: 60,
  duration_seconds: 1.65,
  display_mirror_horizontal: true,
};

describe('CameraReplayDialog', () => {
  it('shows an explicit preparation state before the manually requested MP4 is ready', () => {
    const html = renderToString(
      <CameraReplayDialog replay={replay} state={{ kind: 'preparing' }} onClose={() => {}} onRetry={() => {}} />
    );

    expect(html).toContain('Preparing replay');
    expect(html).not.toContain('<video');
  });

  it('renders a touch player and positions the impact marker from capture metadata', () => {
    const html = renderToString(
      <CameraReplayDialog
        replay={replay}
        state={{ kind: 'ready', videoUrl: 'http://localhost/replay.mp4' }}
        onClose={() => {}}
        onRetry={() => {}}
      />
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain('class="camera-replay__viewport"');
    expect(html).toContain('<video');
    expect(html).toContain('camera-replay__video--mirrored');
    expect(html).toContain('http://localhost/replay.mp4');
    expect(html).toContain('--replay-impact-position:74.48979591836735%');
    expect(html).toContain('aria-label="Impact"');
    expect(html).toContain('Replay from start');
    expect(html).toContain('Playback speed');
    expect(html).toContain('>1×<');
    expect(html).toContain('>0.5×<');
    expect(html).toContain('>0.25×<');
    expect(html).toContain('>0.1×<');
    expect(html).toContain('aria-pressed="false">Loop<');
  });

  it('offers retry and close after preparation fails', () => {
    const html = renderToString(
      <CameraReplayDialog replay={replay} state={{ kind: 'error' }} onClose={() => {}} onRetry={() => {}} />
    );

    expect(html).toContain('Could not prepare replay');
    expect(html).toContain('Try again');
    expect(html).toContain('Close replay');
  });

  it('gives playback failures their own retryable feedback', () => {
    const html = renderToString(
      <CameraReplayDialog
        replay={replay}
        state={{ kind: 'error', stage: 'playback' }}
        onClose={() => {}}
        onRetry={() => {}}
        onPlaybackError={() => {}}
      />
    );

    expect(html).toContain('Could not play replay');
    expect(html).toContain('Try again');
  });

  it('keeps every touch control at least 44 CSS pixels tall', () => {
    const css = readFileSync(fileURLToPath(new URL('./CameraReplayDialog.css', import.meta.url)), 'utf8');

    expect(css).toMatch(/\.camera-replay__button \{[^}]*min-height: 44px/);
    expect(css).toMatch(/\.camera-replay__scrubber \{[^}]*min-height: 44px/);
  });

  it('keeps the scrubber and playback controls in a separate panel beside the video', () => {
    const html = renderToString(
      <CameraReplayDialog
        replay={replay}
        state={{ kind: 'ready', videoUrl: 'http://localhost/replay.mp4' }}
        onClose={() => {}}
        onRetry={() => {}}
      />
    );
    const css = readFileSync(fileURLToPath(new URL('./CameraReplayDialog.css', import.meta.url)), 'utf8');

    expect(html).toMatch(
      /class="camera-replay__body">.*class="camera-replay__stage">.*class="camera-replay__controls">/s
    );
    expect(css).toMatch(/\.camera-replay__body \{[^}]*grid-template-columns: minmax\(0, 1fr\) minmax\(280px, 32%\);/s);
    expect(css).toMatch(/\.camera-replay__stage \{[^}]*grid-area: stage;[^}]*display: grid;[^}]*place-items: center;/s);
    expect(css).toMatch(
      /\.camera-replay__viewport \{[^}]*position: relative;[^}]*width: min\(100%, 720px\);[^}]*aspect-ratio: 16 \/ 9;/s
    );
    expect(css).not.toMatch(/\.camera-replay__viewport \{[^}]*position: absolute/s);
    expect(css).toMatch(/\.camera-replay__controls \{[^}]*grid-area: controls;[^}]*border-left:/s);
  });

  it('mirrors only captures that request an operator-facing correction', () => {
    const unmirroredHtml = renderToString(
      <CameraReplayDialog
        replay={{ ...replay, display_mirror_horizontal: false }}
        state={{ kind: 'ready', videoUrl: 'http://localhost/replay.mp4' }}
        onClose={() => {}}
        onRetry={() => {}}
      />
    );
    const css = readFileSync(fileURLToPath(new URL('./CameraReplayDialog.css', import.meta.url)), 'utf8');

    expect(unmirroredHtml).not.toContain('camera-replay__video--mirrored');
    expect(css).toMatch(/\.camera-replay__video--mirrored \{[^}]*transform: scaleX\(-1\);/s);
  });
});

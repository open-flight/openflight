import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { CameraReplayDialog, type CameraReplayDialogState } from '../../../src/components/CameraReplayDialog';
import { useCameraReplayController } from '../../../src/hooks/useCameraReplayController';
import type { CameraReplay } from '../../../src/types/shot';

const params = new URLSearchParams(window.location.search);
const mode = params.get('mode') ?? 'ready';
const displayMirrorHorizontal = params.get('mirror') !== 'false';

const replayA: CameraReplay = {
  id: 'replay-a',
  frame_count: 99,
  trigger_frame: 73,
  playback_fps: 60,
  duration_seconds: 1.65,
  display_mirror_horizontal: displayMirrorHorizontal,
};

const replayB: CameraReplay = {
  ...replayA,
  id: 'replay-b',
};

export function DirectPlayerFixture() {
  const [state, setState] = useState<CameraReplayDialogState>(
    mode === 'error'
      ? { kind: 'error', stage: 'preparation', message: 'Fixture preparation failed' }
      : { kind: 'ready', videoUrl: '/fixture-replay.mp4' }
  );

  return (
    <CameraReplayDialog
      replay={replayA}
      state={state}
      onClose={() => {}}
      onRetry={() => {
        const fixtureWindow = window as typeof window & { __replayRetryCount?: number };
        fixtureWindow.__replayRetryCount = (fixtureWindow.__replayRetryCount ?? 0) + 1;
        setState({ kind: 'ready', videoUrl: '/fixture-replay.mp4' });
      }}
      onPlaybackError={() => {}}
    />
  );
}

export function RequestControllerFixture() {
  const { activeReplay, openReplay, closeReplay } = useCameraReplayController();

  return (
    <>
      <button type="button" onClick={() => openReplay(replayA)}>
        Open replay A
      </button>
      <button type="button" onClick={() => openReplay(replayB)}>
        Open replay B
      </button>
      {activeReplay ? (
        <CameraReplayDialog
          replay={activeReplay.replay}
          state={activeReplay.state}
          onClose={closeReplay}
          onRetry={() => openReplay(activeReplay.replay)}
          onPlaybackError={() => {}}
        />
      ) : null}
    </>
  );
}

createRoot(document.getElementById('root')!).render(
  mode === 'controller' ? <RequestControllerFixture /> : <DirectPlayerFixture />
);

import { useCallback, useEffect, useRef, useState } from 'react';
import type { CameraReplayDialogState } from '../components/CameraReplayDialog';
import {
  prepareCameraReplay,
  ReplayPreparationRequestError,
  type PreparedCameraReplay,
} from '../services/cameraReplay';
import type { CameraReplay } from '../types/shot';

export interface ActiveCameraReplay {
  replay: CameraReplay;
  state: CameraReplayDialogState;
}

type PrepareReplay = (replayId: string, signal?: AbortSignal) => Promise<PreparedCameraReplay>;

export function useCameraReplayController(prepareReplay: PrepareReplay = prepareCameraReplay) {
  const [activeReplay, setActiveReplay] = useState<ActiveCameraReplay | null>(null);
  const requestRef = useRef<AbortController | null>(null);

  useEffect(() => () => requestRef.current?.abort(), []);

  const openReplay = useCallback(
    (replay: CameraReplay) => {
      requestRef.current?.abort();
      const controller = new AbortController();
      requestRef.current = controller;
      setActiveReplay({ replay, state: { kind: 'preparing' } });

      void prepareReplay(replay.id, controller.signal)
        .then((prepared) => {
          if (requestRef.current !== controller || controller.signal.aborted) return;
          requestRef.current = null;
          setActiveReplay({ replay, state: { kind: 'ready', videoUrl: prepared.videoUrl } });
        })
        .catch((error: unknown) => {
          if (requestRef.current !== controller || controller.signal.aborted) return;
          requestRef.current = null;
          setActiveReplay({
            replay,
            state: {
              kind: 'error',
              stage: 'preparation',
              message: error instanceof ReplayPreparationRequestError ? error.message : undefined,
            },
          });
        });
    },
    [prepareReplay]
  );

  const closeReplay = useCallback(() => {
    requestRef.current?.abort();
    requestRef.current = null;
    setActiveReplay(null);
  }, []);

  const reportPlaybackError = useCallback(() => {
    setActiveReplay((current) =>
      current ? { replay: current.replay, state: { kind: 'error', stage: 'playback' } } : current
    );
  }, []);

  return { activeReplay, openReplay, closeReplay, reportPlaybackError };
}

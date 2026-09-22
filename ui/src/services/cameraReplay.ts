import type { CameraReplay } from '../types/shot';
import { getServerOrigin } from '../utils/serverOrigin';

export interface PreparedCameraReplay extends CameraReplay {
  videoUrl: string;
}

export class ReplayPreparationRequestError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ReplayPreparationRequestError';
  }
}

function isReplayPayload(value: unknown): value is CameraReplay & { video_url: string } {
  if (!value || typeof value !== 'object') return false;
  const replay = value as Record<string, unknown>;
  return (
    typeof replay.id === 'string' &&
    typeof replay.frame_count === 'number' &&
    typeof replay.trigger_frame === 'number' &&
    typeof replay.playback_fps === 'number' &&
    typeof replay.duration_seconds === 'number' &&
    typeof replay.display_mirror_horizontal === 'boolean' &&
    typeof replay.video_url === 'string'
  );
}

export async function prepareCameraReplay(replayId: string, signal?: AbortSignal): Promise<PreparedCameraReplay> {
  const origin = getServerOrigin();
  let response: Response;
  try {
    response = await fetch(`${origin}/api/camera/replays/${encodeURIComponent(replayId)}/prepare`, {
      method: 'POST',
      signal,
    });
  } catch (error) {
    if (error && typeof error === 'object' && (error as { name?: unknown }).name === 'AbortError') {
      throw error;
    }
    throw new ReplayPreparationRequestError('Could not reach the replay service');
  }
  const payload: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const message =
      payload && typeof payload === 'object' && typeof (payload as { error?: unknown }).error === 'string'
        ? (payload as { error: string }).error
        : 'Could not prepare replay';
    throw new ReplayPreparationRequestError(message);
  }
  if (!isReplayPayload(payload)) {
    throw new ReplayPreparationRequestError('The replay response was incomplete');
  }

  return {
    id: payload.id,
    frame_count: payload.frame_count,
    trigger_frame: payload.trigger_frame,
    playback_fps: payload.playback_fps,
    duration_seconds: payload.duration_seconds,
    display_mirror_horizontal: payload.display_mirror_horizontal,
    videoUrl: new URL(payload.video_url, `${origin}/`).toString(),
  };
}

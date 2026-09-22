import { afterEach, describe, expect, it, vi } from 'vitest';
import { prepareCameraReplay, ReplayPreparationRequestError } from './cameraReplay';

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('prepareCameraReplay', () => {
  it('uses the explicit preparation endpoint and returns an absolute video URL', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        id: 'replay 123',
        frame_count: 99,
        trigger_frame: 73,
        playback_fps: 60,
        duration_seconds: 1.65,
        display_mirror_horizontal: false,
        video_url: '/api/camera/replays/replay%20123/video',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    const prepared = await prepareCameraReplay('replay 123');

    expect(fetchMock).toHaveBeenCalledWith(
      'http://localhost:8080/api/camera/replays/replay%20123/prepare',
      expect.objectContaining({ method: 'POST' })
    );
    expect(prepared.display_mirror_horizontal).toBe(false);
    expect(prepared.videoUrl).toBe('http://localhost:8080/api/camera/replays/replay%20123/video');
  });

  it('surfaces the server error without pretending a video exists', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        json: async () => ({ error: 'Could not prepare replay' }),
      })
    );

    await expect(prepareCameraReplay('broken')).rejects.toEqual(
      new ReplayPreparationRequestError('Could not prepare replay')
    );
  });

  it('rejects a replay response without explicit display orientation', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({
          id: 'replay-123',
          frame_count: 99,
          trigger_frame: 73,
          playback_fps: 60,
          duration_seconds: 1.65,
          video_url: '/api/camera/replays/replay-123/video',
        }),
      })
    );

    await expect(prepareCameraReplay('replay-123')).rejects.toEqual(
      new ReplayPreparationRequestError('The replay response was incomplete')
    );
  });

  it('turns a network failure into retryable user feedback', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    await expect(prepareCameraReplay('offline')).rejects.toEqual(
      new ReplayPreparationRequestError('Could not reach the replay service')
    );
  });
});

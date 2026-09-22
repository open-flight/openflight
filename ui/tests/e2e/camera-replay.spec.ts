import { expect, test } from '@playwright/test';
import { gotoApp } from './helpers';

async function mockMediaPlayback(page: import('@playwright/test').Page) {
  await page.addInitScript(() => {
    interface MediaState {
      paused: boolean;
      currentTime: number;
      playbackRate: number;
    }

    const mediaStates = new WeakMap<HTMLMediaElement, MediaState>();
    const stateFor = (media: HTMLMediaElement) => {
      let state = mediaStates.get(media);
      if (!state) {
        state = { paused: true, currentTime: 0, playbackRate: 1 };
        mediaStates.set(media, state);
      }
      return state;
    };

    Object.defineProperties(HTMLMediaElement.prototype, {
      paused: {
        configurable: true,
        get() {
          return stateFor(this as HTMLMediaElement).paused;
        },
      },
      currentTime: {
        configurable: true,
        get() {
          return stateFor(this as HTMLMediaElement).currentTime;
        },
        set(value: number) {
          stateFor(this as HTMLMediaElement).currentTime = Number(value);
          this.dispatchEvent(new Event('timeupdate'));
        },
      },
      playbackRate: {
        configurable: true,
        get() {
          return stateFor(this as HTMLMediaElement).playbackRate;
        },
        set(value: number) {
          stateFor(this as HTMLMediaElement).playbackRate = Number(value);
        },
      },
    });

    HTMLMediaElement.prototype.play = function play() {
      stateFor(this).paused = false;
      this.dispatchEvent(new Event('play'));
      return Promise.resolve();
    };
    HTMLMediaElement.prototype.pause = function pause() {
      stateFor(this).paused = true;
      this.dispatchEvent(new Event('pause'));
    };
  });
}

test('exercises replay controls without covering the video at a kiosk viewport', async ({ page }) => {
  await page.setViewportSize({ width: 800, height: 480 });
  await mockMediaPlayback(page);
  await gotoApp(page, '/tests/e2e/fixtures/camera-replay.html');

  const dialog = page.getByRole('dialog', { name: 'Shot replay' });
  const video = dialog.getByLabel('Strike replay');
  const viewport = dialog.locator('.camera-replay__viewport');
  const controls = dialog.locator('.camera-replay__controls');

  await expect(dialog.getByRole('button', { name: 'Play', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Play', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Pause', exact: true })).toBeVisible();

  await dialog.getByLabel('Scrub replay').fill('0.8');
  await expect(dialog.locator('.camera-replay__time')).toContainText('0:00.8 / 0:01.6');
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBe(0.8);

  await dialog.getByRole('button', { name: '0.25×', exact: true }).click();
  await expect(dialog.getByRole('button', { name: '0.25×', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.playbackRate)).toBe(0.25);

  await dialog.getByRole('button', { name: 'Loop', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Loop', exact: true })).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.loop)).toBe(true);

  await dialog.getByRole('button', { name: 'Replay from start', exact: true }).click();
  await expect.poll(() => video.evaluate((element: HTMLVideoElement) => element.currentTime)).toBe(0);
  await expect(dialog.getByRole('button', { name: 'Pause', exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: 'Pause', exact: true }).click();
  await expect(dialog.getByRole('button', { name: 'Play', exact: true })).toBeVisible();

  const viewportBox = await viewport.boundingBox();
  const controlsBox = await controls.boundingBox();
  expect(viewportBox).not.toBeNull();
  expect(controlsBox).not.toBeNull();
  expect(viewportBox!.x + viewportBox!.width).toBeLessThanOrEqual(controlsBox!.x);
  await expect(video).toHaveCSS('transform', 'matrix(-1, 0, 0, 1, 0, 0)');
});

test('does not mirror a capture already flipped during persistence', async ({ page }) => {
  await gotoApp(page, '/tests/e2e/fixtures/camera-replay.html?mirror=false');

  await expect(page.getByLabel('Strike replay')).toHaveCSS('transform', 'none');
});

test('retries a failed preparation from the player', async ({ page }) => {
  await gotoApp(page, '/tests/e2e/fixtures/camera-replay.html?mode=error');

  await expect(page.getByText('Fixture preparation failed')).toBeVisible();
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.getByLabel('Strike replay')).toBeVisible();
  await expect
    .poll(() => page.evaluate(() => (window as typeof window & { __replayRetryCount?: number }).__replayRetryCount))
    .toBe(1);
});

test('aborts an older preparation and ignores its stale response', async ({ page }) => {
  let releaseReplayA: (() => void) | undefined;
  const replayARelease = new Promise<void>((resolve) => {
    releaseReplayA = resolve;
  });

  await page.route('**/api/camera/replays/*/prepare', async (route) => {
    const replayId = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2) ?? '');
    if (replayId === 'replay-a') {
      await replayARelease;
    }
    await route
      .fulfill({
        contentType: 'application/json',
        body: JSON.stringify({
          id: replayId,
          frame_count: 99,
          trigger_frame: 73,
          playback_fps: 60,
          duration_seconds: 1.65,
          display_mirror_horizontal: true,
          video_url: `/videos/${replayId}.mp4`,
        }),
      })
      .catch(() => undefined);
  });

  await gotoApp(page, '/tests/e2e/fixtures/camera-replay.html?mode=controller');
  await page.getByRole('button', { name: 'Open replay A' }).click();
  await expect(page.getByText('Preparing replay')).toBeVisible();

  await page.getByRole('button', { name: 'Close replay' }).click();
  await page.getByRole('button', { name: 'Open replay B' }).click();
  const video = page.getByLabel('Strike replay');
  await expect(video).toHaveAttribute('src', /replay-b\.mp4$/);

  releaseReplayA?.();
  await page.waitForTimeout(50);
  await expect(video).toHaveAttribute('src', /replay-b\.mp4$/);
});

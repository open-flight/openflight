import { test, type Page } from '@playwright/test';
import { expect, gotoApp } from './helpers';

const SESSION_COUNT = 20;

function sessionId(index: number): string {
  return `session_20261101_${String(100000 + index)}_trackman`;
}

/** Serve a fixed history so the spec does not depend on the host's session logs. */
async function mockHistory(page: Page) {
  const sessions = Array.from({ length: SESSION_COUNT }, (_, index) => ({
    id: sessionId(index),
    filename: `${sessionId(index)}.jsonl`,
    started_at: `2026-11-01T10:${String(index).padStart(2, '0')}:00`,
    location: 'trackman',
    shot_count: 5,
    camera_capture_count: 5,
    hidden: false,
  }));

  await page.route(/\/api\/sessions\/history(\?.*)?$/, (route) =>
    route.fulfill({ json: { sessions, count: sessions.length } })
  );
  await page.route(/\/api\/sessions\/history\/session_[^/]+$/, (route) => {
    const id = new URL(route.request().url()).pathname.split('/').pop() ?? '';
    return route.fulfill({
      json: {
        id,
        started_at: '2026-11-01T10:00:00',
        shot_count: 1,
        shots: [
          {
            shot_number: 1,
            timestamp: '2026-11-01T10:00:05',
            club: '7-iron',
            ball_speed_mph: 110,
            club_speed_mph: 80,
            estimated_carry_yards: 150,
            smash_factor: 1.38,
            spin_rpm: 6500,
            camera_available: true,
            recorded_data: {},
          },
        ],
      },
    });
  });
}

async function openHistory(page: Page) {
  await mockHistory(page);
  await gotoApp(page);
  await page.getByRole('button', { name: 'Close Select club' }).click();
  await page.getByRole('button', { name: 'History', exact: true }).click();
}

test('scrolls the history list by dragging on a session', async ({ page }) => {
  await page.setViewportSize({ width: 1024, height: 600 });
  await openHistory(page);

  const list = page.getByRole('region', { name: 'Saved sessions' });
  await expect(list.locator('.history-session-item')).toHaveCount(SESSION_COUNT);

  const before = await list.evaluate((node) => node.scrollTop);
  const box = await list.boundingBox();
  expect(box).toBeTruthy();

  await page.mouse.move(box!.x + box!.width / 3, box!.y + 220);
  await page.mouse.down();
  await page.mouse.move(box!.x + box!.width / 3, box!.y + 40, { steps: 12 });
  await page.mouse.up();

  await expect.poll(async () => list.evaluate((node) => node.scrollTop)).toBeGreaterThan(before);
  // The drag must not have opened the session under the pointer.
  await expect(page.getByRole('region', { name: 'Saved sessions' })).toBeVisible();
  await expect(page.getByRole('button', { name: /All sessions/ })).toHaveCount(0);
});

test.describe('touch', () => {
  test.use({ hasTouch: true });

  test('a tap still opens one session and its shots start at the top', async ({ page }) => {
    await page.setViewportSize({ width: 1024, height: 600 });
    await openHistory(page);

    const list = page.getByRole('region', { name: 'Saved sessions' });
    await list.evaluate((node) => {
      node.scrollTop = node.scrollHeight;
    });
    await list.locator('.history-session-row').last().tap();

    const shots = page.getByRole('region', { name: 'Recorded shots in session' });
    await expect(shots).toBeVisible();
    await expect(shots.getByRole('button', { name: '▶ Replay video' })).toBeVisible();
    expect(await shots.evaluate((node) => node.scrollTop)).toBe(0);
  });
});

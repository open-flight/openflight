import { expect, test, type Page, type Route } from '@playwright/test';
import { gotoApp, KIOSK_VIEWPORTS } from './helpers';

const targetPath = '/openflight-target';

const startingStatus = {
  version: 1,
  overall: 'starting',
  message: 'Connecting TI radar',
  components: [
    { id: 'server', label: 'OpenFlight server', state: 'ready' },
    { id: 'ops', label: 'OPS radar', state: 'ready' },
    { id: 'ti', label: 'TI radar', state: 'starting' },
    { id: 'camera', label: 'Camera', state: 'waiting' },
  ],
};

const failedStatus = {
  ...startingStatus,
  overall: 'error',
  message: 'TI radar failed to initialize',
  components: startingStatus.components.map((component) =>
    component.id === 'ti' ? { ...component, state: 'error' } : component
  ),
  error: {
    recovery: 'Press RESET on the TI board, then relaunch OpenFlight.',
    log_path: '/home/pacinoj/openflight_sessions/terminal_logs/latest.log',
  },
};

async function fulfillJson(route: Route, payload: object) {
  await route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify(payload),
  });
}

async function gotoSplash(page: Page) {
  await gotoApp(page, `/startup-splash.html?target=${encodeURIComponent(targetPath)}`);
}

async function expectMainInsideViewport(page: Page) {
  const overflow = await page.locator('main').evaluate((main) => {
    const rect = main.getBoundingClientRect();
    return {
      left: rect.left,
      top: rect.top,
      right: rect.right - window.innerWidth,
      bottom: rect.bottom - window.innerHeight,
    };
  });
  expect(overflow.left).toBeGreaterThanOrEqual(0);
  expect(overflow.top).toBeGreaterThanOrEqual(0);
  expect(overflow.right).toBeLessThanOrEqual(0);
  expect(overflow.bottom).toBeLessThanOrEqual(0);
}

test('renders component progress and updates states from status polling', async ({ page }) => {
  let status = startingStatus;
  await page.route('**/status.json', (route) => fulfillJson(route, status));
  await page.route(`**${targetPath}`, (route) => route.abort());

  await gotoSplash(page);

  await expect(page.getByText('Connecting TI radar')).toBeVisible();
  await expect(page.locator('.component.ready')).toHaveCount(2);
  await expect(page.locator('.component.starting')).toContainText('TI radar');
  await expect(page.locator('.component.waiting')).toContainText('Camera');

  status = {
    ...startingStatus,
    message: 'Camera unavailable; continuing',
    components: startingStatus.components.map((component) => {
      if (component.id === 'ti') return { ...component, state: 'ready' };
      if (component.id === 'camera') return { ...component, state: 'skipped' };
      return component;
    }),
  };

  await expect(page.locator('.component.ready')).toHaveCount(3);
  await expect(page.locator('.component.skipped')).toContainText('Camera');
  await expect(page.getByText('Camera unavailable; continuing')).toBeVisible();
});

test('hands off to OpenFlight as soon as its target responds', async ({ page }) => {
  let targetRequests = 0;
  await page.route('**/status.json', (route) => fulfillJson(route, startingStatus));
  await page.route(`**${targetPath}`, async (route) => {
    targetRequests += 1;
    if (targetRequests === 1) {
      await route.abort();
      return;
    }
    await route.fulfill({ contentType: 'text/html', body: '<h1>OpenFlight ready</h1>' });
  });

  await gotoSplash(page);

  await expect(page).toHaveURL(new RegExp(`${targetPath}$`));
  await expect(page.getByRole('heading', { name: 'OpenFlight ready' })).toBeVisible();
  expect(targetRequests).toBeGreaterThanOrEqual(2);
});

test('keeps a startup failure visible and stops polling or handoff retries', async ({ page }) => {
  let statusRequests = 0;
  let targetRequests = 0;
  await page.route('**/status.json', async (route) => {
    statusRequests += 1;
    await fulfillJson(route, failedStatus);
  });
  await page.route(`**${targetPath}`, async (route) => {
    targetRequests += 1;
    await route.abort();
  });

  await gotoSplash(page);

  await expect(page.getByRole('heading', { name: 'OpenFlight couldn’t start' })).toBeVisible();
  await expect(page.getByText(failedStatus.error.recovery)).toBeVisible();
  await expect(page.getByText(failedStatus.error.log_path)).toBeVisible();
  const requestCounts = { statusRequests, targetRequests };
  await page.waitForTimeout(750);

  expect({ statusRequests, targetRequests }).toEqual(requestCounts);
  await expect(page).toHaveURL(/startup-splash\.html/);
});

test('posts the dismissal request and disables repeated taps', async ({ page }) => {
  let dismissMethod: string | undefined;
  await page.route('**/status.json', (route) => fulfillJson(route, failedStatus));
  await page.route(`**${targetPath}`, (route) => route.abort());
  await page.route('**/dismiss', async (route) => {
    dismissMethod = route.request().method();
    await route.fulfill({ status: 204 });
  });
  await gotoSplash(page);

  const dismiss = page.locator('#dismiss');
  await expect(dismiss).toBeVisible();
  await expect(dismiss).toHaveText('Return to desktop');
  await dismiss.click();

  await expect(dismiss).toBeDisabled();
  await expect(dismiss).toHaveText('Closing…');
  expect(dismissMethod).toBe('POST');
});

for (const viewport of KIOSK_VIEWPORTS) {
  test(`fits progress and failure states at ${viewport.width}×${viewport.height}`, async ({ page }) => {
    let status = startingStatus;
    await page.setViewportSize(viewport);
    await page.route('**/status.json', (route) => fulfillJson(route, status));
    await page.route(`**${targetPath}`, (route) => route.abort());

    await gotoSplash(page);
    await expect(page.getByRole('heading', { name: 'Starting OpenFlight' })).toBeVisible();
    await expectMainInsideViewport(page);

    status = failedStatus;
    await expect(page.getByRole('button', { name: 'Return to desktop' })).toBeVisible();
    await expectMainInsideViewport(page);
  });
}

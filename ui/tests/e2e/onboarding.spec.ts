import { test } from '@playwright/test';
import { expect, gotoApp, simulateShot, withControlSocket } from './helpers';

test('first visit shows welcome, not the club picker', async ({ page }) => {
  await gotoApp(page, '/', { onboarded: false });
  await expect(page.getByRole('button', { name: 'Get started' })).toBeVisible();
  await expect(page.getByRole('dialog', { name: 'Select club' })).toHaveCount(0);
  await expect(page.getByRole('button', { name: 'Live' })).toHaveCount(0);
});

test('walks setup and lands on Live with Driver and no picker', async ({ page }) => {
  await gotoApp(page, '/', { onboarded: false });
  await page.getByRole('button', { name: 'Get started' }).click();
  await page.getByRole('button', { name: 'Español' }).click();
  await expect(page.getByRole('button', { name: 'Continuar' })).toBeVisible();
  await page.getByRole('button', { name: 'English' }).click();
  await page.getByRole('button', { name: 'KMH / M' }).click();
  await page.getByRole('button', { name: 'MPH / YDS' }).click();
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('button', { name: 'Light' }).click();
  await page.getByRole('button', { name: 'Dark' }).click();
  await page.getByRole('button', { name: 'Continue' }).click();
  await expect(page.getByRole('button', { name: '5s', exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: 'Timed preview' }).click();
  await expect(page.getByRole('button', { name: '5s', exact: true })).toBeVisible();
  await page.getByRole('button', { name: /^Tiles/ }).click();
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('button', { name: 'Start' }).click();
  await expect(page.getByRole('dialog', { name: 'Select club' })).toHaveCount(0);
  await expect(page.locator('.panel-header__club')).toContainText('Driver');
  await expect(page.getByRole('button', { name: 'Live' })).toBeVisible();
});

test('later launch opens the club picker', async ({ page }) => {
  await gotoApp(page, '/', { onboarded: false });
  await page.getByRole('button', { name: 'Get started' }).click();
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('button', { name: 'Continue' }).click();
  await page.getByRole('button', { name: 'Start' }).click();
  await page.reload();
  await expect(page.getByRole('dialog', { name: 'Select club' })).toBeVisible();
});

test('display route never shows onboarding', async ({ page }) => {
  await gotoApp(page, '/display', { onboarded: false });
  await expect(page.getByText('OpenFlight Display')).toBeVisible();
  await expect(page.getByRole('button', { name: 'Get started' })).toHaveCount(0);
});

test('menu has live view and no shutdown; header still shuts down', async ({ page }) => {
  await gotoApp(page);
  await page.getByRole('button', { name: 'Close Select club' }).click();
  await page.getByRole('button', { name: 'Open menu' }).click();
  const menu = page.getByRole('dialog', { name: 'Menu' });
  await expect(menu.getByText('Live view', { exact: true })).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Tiles', exact: true })).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Timed', exact: true })).toBeVisible();
  await expect(menu.getByRole('button', { name: 'Hold', exact: true })).toBeVisible();
  await expect(menu.getByText('System', { exact: true })).toHaveCount(0);
  await expect(menu.getByRole('button', { name: 'Shut down' })).toHaveCount(0);
  await page.getByRole('button', { name: 'Close menu' }).click();
  await page.locator('.panel-header__power').click();
  await expect(page.getByRole('dialog', { name: 'Shut down OpenFlight?' })).toBeVisible();
});

test.describe('menu live view at 800×400', () => {
  test.use({ viewport: { width: 800, height: 400 } });

  test('scrolls the menu by dragging so duration chips can be selected', async ({ page }) => {
    await gotoApp(page);
    await page.getByRole('button', { name: 'Close Select club' }).click();
    await page.getByRole('button', { name: 'Open menu' }).click();
    const menu = page.getByRole('dialog', { name: 'Menu' });
    await menu.getByRole('button', { name: 'Timed', exact: true }).click();

    await expect.poll(async () => menu.evaluate((node) => node.scrollHeight > node.clientHeight)).toBe(true);

    const before = await menu.evaluate((node) => node.scrollTop);
    const box = await menu.boundingBox();
    expect(box).toBeTruthy();

    await page.mouse.move(box!.x + box!.width / 2, box!.y + box!.height - 24);
    await page.mouse.down();
    await page.mouse.move(box!.x + box!.width / 2, box!.y + 24, { steps: 12 });
    await page.mouse.up();

    await expect.poll(async () => menu.evaluate((node) => node.scrollTop)).toBeGreaterThan(before);
    await expect(menu.getByRole('button', { name: 'Timed', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await menu.getByRole('button', { name: '5s', exact: true }).click();
    await expect(menu.getByRole('button', { name: '5s', exact: true })).toHaveAttribute('aria-pressed', 'true');
  });
});

test.describe('menu on a touchscreen at 800×400', () => {
  test.use({ hasTouch: true, viewport: { width: 800, height: 400 } });

  test('taps Timed without a drag', async ({ page }) => {
    await gotoApp(page);
    await page.getByRole('button', { name: 'Close Select club' }).click();
    await page.getByRole('button', { name: 'Open menu' }).click();
    const menu = page.getByRole('dialog', { name: 'Menu' });

    await menu.getByRole('button', { name: 'Timed', exact: true }).tap();
    await expect(menu.getByRole('button', { name: 'Timed', exact: true })).toHaveAttribute('aria-pressed', 'true');
    await expect(menu.getByRole('button', { name: '5s', exact: true })).toHaveCount(1);
  });
});

test('timed overlay hides after the chosen duration; hold stays until tap', async ({ page }) => {
  await page.clock.install();
  await gotoApp(page);
  await page.getByRole('button', { name: 'Close Select club' }).click();

  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });
  await expect(page.locator('.live-panel__spotlight')).toHaveCount(0);

  await page.getByRole('button', { name: 'Open menu' }).click();
  await page.getByRole('button', { name: 'Timed', exact: true }).click();
  await page.getByRole('button', { name: '5s', exact: true }).click();
  await page.getByRole('button', { name: 'Close menu' }).click();

  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });
  await expect(page.locator('.live-panel__spotlight')).toBeVisible();
  await page.clock.fastForward(5000);
  await expect(page.locator('.live-panel__spotlight')).toHaveCount(0);

  await page.getByRole('button', { name: 'Open menu' }).click();
  await page.getByRole('button', { name: 'Hold', exact: true }).click();
  await page.getByRole('button', { name: 'Close menu' }).click();

  await withControlSocket(async (socket) => {
    await simulateShot(socket);
  });
  await expect(page.locator('.live-panel__spotlight')).toBeVisible();
  await page.locator('.live-panel__spotlight').click();
  await expect(page.locator('.live-panel__spotlight')).toHaveCount(0);
});

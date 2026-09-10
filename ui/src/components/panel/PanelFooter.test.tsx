import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PanelFooter } from './PanelFooter';
import type { PanelView } from './views';
import type { PowerStatus } from '../../types/power';

function render(
  currentView: PanelView = 'live',
  shotCount = 0,
  powerStatus: PowerStatus | null | undefined = undefined
) {
  return renderToString(
    <PanelFooter
      currentView={currentView}
      onChangeView={() => {}}
      onOpenMenu={() => {}}
      menuOpen={false}
      shotCount={shotCount}
      cameraStreaming={false}
      ballDetected={false}
      debugRecording={false}
      powerStatus={powerStatus}
    />
  ).replace(/<!-- -->/g, '');
}

const batteryStatus = (overrides: Partial<PowerStatus> = {}): PowerStatus => ({
  available: true,
  provider: 'geekworm',
  state: 'on_battery',
  battery_percent: 64.2,
  battery_voltage_v: 3.81,
  external_power: false,
  updated_at: '2026-08-20T12:00:00+00:00',
  error: null,
  ...overrides,
});

describe('PanelFooter', () => {
  it('puts units on the right of Live without a shot count', () => {
    const html = render('live', 55);

    expect(html).toContain('panel-footer__meta');
    expect(html).toContain('panel-footer__units');
    expect(html).toContain('mph / yds');
    expect(html).not.toContain('Shot 55');
    expect(html).toContain('nav__badge">55<');
  });

  it('puts units on Stats and Shots without a footer shot count', () => {
    for (const view of ['stats', 'shots'] as const) {
      const html = render(view, 4);

      expect(html).toContain('mph / yds');
      expect(html).not.toContain('Shot 04');
      expect(html).toContain('nav__badge">4<');
    }
  });

  it('hides units, battery, and shutdown on Profiles, Camera, and Debug', () => {
    for (const view of ['profiles', 'camera', 'debug'] as const) {
      const html = render(view, 4, null);

      expect(html).not.toContain('panel-footer__meta');
      expect(html).not.toContain('mph / yds');
      expect(html).not.toContain('power-status');
      expect(html).not.toContain('panel-footer__power');
    }
  });

  it('hides the battery when telemetry is null', () => {
    const html = render('live', 0, null);

    expect(html).toContain('panel-footer__units');
    expect(html).not.toContain('power-status');
  });

  it('shows live battery percentage to the right of units', () => {
    const html = render('live', 0, batteryStatus());

    expect(html).toContain('panel-footer__units');
    expect(html).toContain('power-status--chrome');
    expect(html).toContain('64%');
    const unitsAt = html.indexOf('panel-footer__units');
    const batteryAt = html.indexOf('power-status--chrome');
    expect(unitsAt).toBeGreaterThan(-1);
    expect(batteryAt).toBeGreaterThan(unitsAt);
  });

  it('keeps the battery on Camera when telemetry is present', () => {
    const html = render('camera', 0, batteryStatus({ battery_percent: 41 }));

    expect(html).toContain('panel-footer__meta');
    expect(html).not.toContain('mph / yds');
    expect(html).toContain('41%');
  });

  it('does not keep shutdown in the footer on any panel', () => {
    for (const view of ['live', 'stats', 'shots', 'camera', 'profiles', 'debug'] as const) {
      const html = render(view, 0, null);

      expect(html).not.toContain('panel-footer__power');
      expect(html).not.toContain('aria-label="Shut down"');
    }
  });

  it('marks the active tab pressed', () => {
    const html = render('live');
    const liveButton = html.match(/<button[^>]*nav__button[^>]*>[\s\S]*?<span>Live<\/span>[\s\S]*?<\/button>/)?.[0];

    expect(liveButton).toBeDefined();
    expect(liveButton).toContain('nav__button--active');
    expect(liveButton).toContain('aria-pressed="true"');
  });

  it('separates tabs with the same hairline divider as the header', () => {
    const html = render('live');

    expect(html).toContain('panel-footer__nav');
    expect(html).toContain('panel-footer__tabs');
    expect(html).toContain('aria-label="Panels"');
    expect(html.match(/panel-header__divider/g)).toHaveLength(5);
  });
});

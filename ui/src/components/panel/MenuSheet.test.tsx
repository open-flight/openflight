import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { MenuSheet } from './MenuSheet';
import { useSystemStore } from '../../stores/useSystemStore';
import { useLiveViewStore } from '../../stores/useLiveViewStore';
import type { PowerStatus } from '../../types/power';

function renderMenu() {
  return renderToString(<MenuSheet onClose={() => {}} />);
}

describe('MenuSheet profiles', () => {
  it('does not manage profiles in the menu', () => {
    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__section-title">Profile');
    expect(html).not.toContain('Add profile');
    expect(html).not.toContain('menu-sheet__input');
  });
});

describe('MenuSheet language', () => {
  it('offers a language dropdown with the shipped locales', () => {
    const html = renderMenu();

    expect(html).toContain('menu-sheet__section-title">Language');
    expect(html).toContain('aria-label="Language"');
    expect(html).toContain('>English</option>');
    expect(html).toContain('>Español</option>');
    expect(html).toContain('>Français</option>');
    expect(html).toContain('>Português</option>');
  });
});

describe('MenuSheet battery', () => {
  it('does not show battery in the menu, even when telemetry is present', () => {
    const powerStatus: PowerStatus = {
      available: true,
      provider: 'geekworm',
      state: 'on_battery',
      battery_percent: 64.2,
      battery_voltage_v: 3.81,
      external_power: false,
      updated_at: '2026-08-20T12:00:00+00:00',
      error: null,
    };
    useSystemStore.setState({ powerStatus });

    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__status-label">Battery');
    expect(html).not.toContain('power-status');
    expect(html).not.toContain('64%');

    useSystemStore.setState({ powerStatus: null });
  });
});

describe('MenuSheet live view', () => {
  it('offers live view modes and hides duration unless timed', () => {
    useLiveViewStore.setState({ mode: 'tiles', durationMs: 10000 });
    const html = renderMenu();

    expect(html).toContain('menu-sheet__section-title">Live view');
    expect(html).toContain('>Tiles<');
    expect(html).toContain('>Timed<');
    expect(html).toContain('>Hold<');
    expect(html).not.toContain('>5s<');
  });

  it('does not show system, ball detection, or simulators', () => {
    const html = renderMenu();

    expect(html).not.toContain('menu-sheet__section-title">System');
    expect(html).not.toContain('Ball detection');
    expect(html).not.toContain('Simulators');
  });

  it('shows duration chips when timed is selected', () => {
    useLiveViewStore.setState({ mode: 'timed', durationMs: 10000 });
    const html = renderMenu();

    expect(html).toContain('>5s<');
    expect(html).toContain('>10s<');
    expect(html).toContain('>15s<');
  });

  it('owns vertical drag scrolling so Timed duration chips can be reached', () => {
    const src = readFileSync(fileURLToPath(new URL('./MenuSheet.tsx', import.meta.url)), 'utf8');
    const css = readFileSync(fileURLToPath(new URL('./panel.css', import.meta.url)), 'utf8');

    expect(src).toContain('useDragScroll');
    expect(css).toMatch(/\.menu-sheet \{[^}]*touch-action:\s*none/);
    expect(css).toMatch(/\.menu-sheet \.segmented-control__button \{[^}]*touch-action:\s*none/);
  });
});

describe('MenuSheet shutdown', () => {
  it('does not offer shut down in the sheet', () => {
    const html = renderMenu();
    expect(html).not.toContain('menu-sheet__shutdown');
    expect(html).not.toContain('Shut down');
  });
});

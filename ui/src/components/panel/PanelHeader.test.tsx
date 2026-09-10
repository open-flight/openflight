import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { PanelHeader } from './PanelHeader';

describe('PanelHeader', () => {
  it('marks the status dot disconnected by default', () => {
    const html = renderToString(<PanelHeader title="Live" />);

    expect(html).toContain('panel-header__dot--disconnected');
    expect(html).toContain('aria-label="Server disconnected"');
    expect(html).not.toContain('panel-header__dot--connected');
  });

  it('turns the status dot green when the server is connected', () => {
    const html = renderToString(<PanelHeader title="Live" connected />);

    expect(html).toContain('panel-header__dot--connected');
    expect(html).toContain('aria-label="Server connected"');
    expect(html).toContain('aria-expanded="false"');
  });

  it('shows the club after the subtitle, each behind a divider', () => {
    const html = renderToString(<PanelHeader title="Live" subtitle="James" club="DR" />);

    expect(html).toContain('panel-header__subtitle">James<');
    expect(html).toContain('panel-header__club">DR<');
    expect(html.match(/panel-header__divider/g)).toHaveLength(2);
  });

  it('keeps the status menu closed until the LED and title are opened', () => {
    const html = renderToString(<PanelHeader title="Live" connected />);

    expect(html).not.toContain('aria-label="System status"');
    expect(html).toContain('panel-header__status');
    expect(html).toContain('panel-header__title">Live<');
  });

  it('opens a status menu with server, radar, and ball detection in words', () => {
    const html = renderToString(
      <PanelHeader
        title="Shots"
        connected
        radarConnected
        statusMenuOpen
        cameraStatus={{
          available: true,
          enabled: true,
          streaming: false,
          ball_detected: true,
          ball_confidence: 0.91,
        }}
      />
    );

    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('panel-scrim');
    expect(html).toContain('aria-label="System status"');
    expect(html).toContain('>Server<');
    expect(html).toContain('>Radar<');
    expect(html).toContain('>Ball detection<');
    expect(html).toContain('>Connected<');
    expect(html).toContain('>Ball 91%<');
    expect(html).not.toContain('GSPro');
  });

  it('shows simulator statuses in the open status menu', () => {
    const html = renderToString(
      <PanelHeader
        title="Live"
        connected
        statusMenuOpen
        simStatuses={{ gspro: { target: 'gspro', state: 'connecting' } }}
      />
    );

    expect(html).toContain('>Simulators<');
    expect(html).toContain('GSPro');
    expect(html).toContain('connecting');
    expect(html).toContain('sim-status__pill--warn');
  });

  it('renders right-hand actions', () => {
    const html = renderToString(<PanelHeader title="Live" actions={<button type="button">Change club</button>} />);

    expect(html).toContain('panel-header__actions');
    expect(html).toContain('Change club');
  });

  it('pins shutdown on the right with a divider after other actions', () => {
    const html = renderToString(<PanelHeader title="Live" actions={<button type="button">Change club</button>} />);
    const actions = html.match(/panel-header__actions[\s\S]*<\/div>/)?.[0];

    expect(html).toContain('panel-header__power');
    expect(html).toContain('aria-label="Shut down"');
    expect(actions).toBeDefined();
    expect(actions).toContain('Change club');
    expect(actions).toContain('panel-header__divider');
    expect(actions!.indexOf('Change club')).toBeLessThan(actions!.indexOf('panel-header__divider'));
    expect(actions!.indexOf('panel-header__divider')).toBeLessThan(actions!.indexOf('panel-header__power'));
  });

  it('keeps shutdown on the right without a divider when there are no other actions', () => {
    const html = renderToString(<PanelHeader title="Live" />);

    expect(html).toContain('panel-header__power');
    expect(html).toContain('aria-label="Shut down"');
    expect(html).not.toContain('panel-header__divider');
  });

  it('sizes the power control to the same header chip as panel actions', () => {
    const css = readFileSync(fileURLToPath(new URL('./panel.css', import.meta.url)), 'utf8');
    const power = css.match(/\.panel-header__power \{[^}]+\}/)?.[0];
    const headerAction = css.match(/\.panel-header__actions \.panel-action \{[^}]+\}/)?.[0];

    expect(power).toContain('height: var(--panel-control-height, 32px)');
    expect(power).toContain('width: var(--panel-control-height, 32px)');
    expect(power).toContain('border-radius: 8px');
    expect(headerAction).toContain('height: var(--panel-control-height, 32px)');
    expect(css).toMatch(/\n\.panel-action \{[^}]*border-radius: 8px/);
    expect(power).not.toContain('height: 44px');
  });
});

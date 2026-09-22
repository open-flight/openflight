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

  it('opens a status menu with server and radar state', () => {
    const html = renderToString(<PanelHeader title="Shots" connected radarConnected statusMenuOpen />);

    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain('panel-scrim');
    expect(html).toContain('aria-label="System status"');
    expect(html).toContain('>Server<');
    expect(html).toContain('>Radar<');
    expect(html).toContain('>Connected<');
  });

  it('renders right-hand actions', () => {
    const html = renderToString(<PanelHeader title="Live" actions={<button type="button">Change club</button>} />);

    expect(html).toContain('panel-header__actions');
    expect(html).toContain('Change club');
  });
});

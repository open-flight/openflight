import { renderToString } from 'react-dom/server';
import { beforeEach, describe, expect, it } from 'vitest';
import App from './App';
import { PANEL_VIEWS } from './components/panel';
import { useSystemStore } from './stores/useSystemStore';

describe('App shell', () => {
  beforeEach(() => {
    useSystemStore.setState({ serverClub: null });
  });

  it('renders the bottom bar instead of the old top header', () => {
    const html = renderToString(<App />);

    expect(html).toContain('panel-footer');
    expect(html).toContain('aria-label="Open menu"');
    // The 6a chrome replaced the header entirely; nothing from it should survive.
    expect(html).not.toContain('class="header"');
    expect(html).not.toContain('header__secret-tap');
    expect(html).not.toContain('unit-toggle');
  });

  it('renders every panel tab, with Profiles as a first-class view', () => {
    const html = renderToString(<App />);

    for (const view of PANEL_VIEWS) {
      expect(html).toContain(`<span>${view.label}</span>`);
    }
    expect(PANEL_VIEWS.map((view) => view.id)).toEqual(['live', 'stats', 'shots', 'camera', 'profiles', 'debug']);
  });

  it('marks the Live tab pressed and shows the Live panel', () => {
    const html = renderToString(<App />);
    const liveButton = html.match(/<button[^>]*nav__button[^>]*>[\s\S]*?<span>Live<\/span>[\s\S]*?<\/button>/)?.[0];

    expect(liveButton).toBeDefined();
    expect(liveButton).toContain('aria-pressed="true"');
    expect(html).toContain('panel-header__title">Live<');
  });

  it('opens on the club picker so the club is confirmed before the first shot', () => {
    const html = renderToString(<App />);

    expect(html).toContain('aria-label="Select club"');
    // Driver is the default and is pre-selected.
    expect(html).toMatch(/picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*>DR</);
    // ...and it is dismissible, so skipping keeps the default.
    expect(html).toContain('aria-label="Close Select club"');
  });

  it('offers the club change action in the Live header', () => {
    const html = renderToString(<App />);

    expect(html).toContain('Change club');
    expect(html).not.toContain('panel-footer__action');
    expect(html).not.toContain('panel-action__value');
    expect(html).toContain('panel-header__club">Driver<');
  });

  it('puts units in the Live footer', () => {
    const html = renderToString(<App />);

    expect(html).toContain('panel-footer__units');
    expect(html).toContain('mph / yds');
    expect(html).not.toContain('Shot 00');
    expect(html).not.toContain('panel-footer__count');
  });

  it('keeps the shutdown power control in the footer', () => {
    const html = renderToString(<App />);

    expect(html).toContain('panel-footer__power');
    expect(html).toContain('aria-label="Shut down"');
  });

  it('does not ask to clear a session until the stats action is used', () => {
    const html = renderToString(<App />);

    expect(html).not.toContain("Clear Profile 1's session?");
    expect(html).not.toContain('Clear Profile 1&#x27;s session?');
    expect(html).not.toContain('clear-session-title');
  });
});

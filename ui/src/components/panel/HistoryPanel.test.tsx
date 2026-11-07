import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { HistoryPanel } from './HistoryPanel';

const css = readFileSync(fileURLToPath(new URL('./panel.css', import.meta.url)), 'utf8');

function rule(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  // Rules end at a closing brace on its own line; comments may contain braces.
  return css.match(new RegExp(`(?:^|\\n)${escaped} \\{([\\s\\S]*?)\\n\\}`))?.[1] ?? '';
}

function render(): string {
  return renderToString(<HistoryPanel onReplayShot={() => undefined} />);
}

describe('HistoryPanel', () => {
  it('exposes the session list as a drag-scrollable region', () => {
    const html = render();

    expect(html).toContain('panel__body history-content');
    expect(html).toContain('role="region"');
    expect(html).toContain('aria-label="Saved sessions"');
  });

  it('lets the scroller own touch gestures on the kiosk', () => {
    const scroller = rule('.history-content');

    expect(scroller).toMatch(/overflow-y: auto/);
    expect(scroller).toMatch(/touch-action: none/);
    expect(scroller).toMatch(/user-select: none/);
    expect(scroller).toMatch(/overscroll-behavior: contain/);
  });

  it('keeps tappable children from swallowing drags', () => {
    expect(css).toMatch(
      /\.history-content button,\n\.history-content summary,\n\.history-toggle,\n\.history-toggle input \{\n {2}touch-action: none;/
    );
  });

  it('renders buttons in the app font instead of the browser default', () => {
    expect(rule('.history-content button')).toMatch(/font: inherit/);
  });

  it.each([
    '.history-toggle',
    '.history-visibility',
    '.history-replay',
    '.history-back',
    '.history-detail-section > summary',
  ])('gives %s a 44px touch target', (selector) => {
    expect(rule(selector)).toMatch(/min-height: 44px/);
  });

  it('renders the filters as labelled toggles', () => {
    const html = render().replace(/<!-- -->/g, '');

    for (const label of ['Short sessions (1–4)', 'Show hidden', 'Show empty']) {
      expect(html).toContain(label);
    }
    expect(html.match(/class="history-toggle"/g)).toHaveLength(3);
  });
});

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SegmentedControl } from './SegmentedControl';

function readCss() {
  return readFileSync(fileURLToPath(new URL('./SegmentedControl.css', import.meta.url)), 'utf8');
}

describe('SegmentedControl', () => {
  it('marks the selected option pressed', () => {
    const html = renderToString(
      <SegmentedControl
        ariaLabel="Theme"
        value="dark"
        options={[
          { id: 'dark', label: 'DARK' },
          { id: 'light', label: 'LIGHT' },
        ]}
        onChange={() => {}}
      />
    );
    expect(html).toContain('DARK');
    expect(html).toContain('aria-pressed="true"');
    expect(html).toContain('segmented-control__button--active');
    expect(html).toContain('aria-pressed="false"');
  });

  it('keeps every option at least 44 CSS pixels tall, on every viewport', () => {
    const css = readCss();

    expect(css).toMatch(/\.segmented-control__button \{[^}]*min-height: 44px/);

    // Guard against a *future* min-height rule shrinking the touch target
    // again under some other condition (not necessarily this exact
    // @media block) — every min-height declared anywhere in this file
    // must be exactly 44px, full stop.
    const minHeights = css.match(/min-height:\s*[\d.]+px/g) ?? [];
    expect(minHeights).toEqual(['min-height: 44px']);
  });
});

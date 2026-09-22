import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { SegmentedControl } from './SegmentedControl';

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
});

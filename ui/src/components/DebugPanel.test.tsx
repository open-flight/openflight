import type { ReactElement } from 'react';
import { renderToString } from 'react-dom/server';
import { describe, expect, it, vi } from 'vitest';
import { RadarLedsControl } from './DebugPanel';
import type { RadarConfig } from '../types/socket';

type Update = (config: Partial<RadarConfig>) => void;

function segmentedOnChange(leds: boolean, onUpdateConfig: Update) {
  const row = RadarLedsControl({ leds, disabled: false, onUpdateConfig }) as ReactElement<{
    children: ReactElement<{ onChange: (id: string) => void }>[];
  }>;
  return row.props.children[1].props.onChange;
}

describe('RadarLedsControl', () => {
  it('is hidden when the server does not report leds', () => {
    expect(renderToString(<RadarLedsControl disabled={false} onUpdateConfig={() => {}} />)).toBe('');
  });

  it('shows the current LED state as the pressed option', () => {
    const html = renderToString(<RadarLedsControl leds={false} disabled={false} onUpdateConfig={() => {}} />);

    expect(html).toContain('aria-label="Radar LEDs"');
    expect(html).toMatch(/aria-pressed="true"[^>]*>Off</);
    expect(html).toMatch(/aria-pressed="false"[^>]*>On</);
    expect(html).not.toContain('slider-control--disabled');
  });

  it('dims the control when tuning is disabled', () => {
    const html = renderToString(<RadarLedsControl leds disabled onUpdateConfig={() => {}} />);

    expect(html).toContain('slider-control--disabled');
  });

  it('sends only the flipped leds value when the other option is tapped', () => {
    const onUpdateConfig = vi.fn<Update>();

    segmentedOnChange(true, onUpdateConfig)('off');
    segmentedOnChange(false, onUpdateConfig)('on');

    expect(onUpdateConfig.mock.calls).toEqual([[{ leds: false }], [{ leds: true }]]);
  });

  it('sends nothing when the already-selected option is tapped', () => {
    const onUpdateConfig = vi.fn<Update>();

    segmentedOnChange(true, onUpdateConfig)('on');
    segmentedOnChange(false, onUpdateConfig)('off');

    expect(onUpdateConfig).not.toHaveBeenCalled();
  });
});

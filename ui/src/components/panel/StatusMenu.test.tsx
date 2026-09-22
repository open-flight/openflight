import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { StatusMenu } from './StatusMenu';

describe('StatusMenu', () => {
  it('states server and radar connectivity in words', () => {
    const html = renderToString(<StatusMenu connected radarConnected={false} onClose={() => {}} />);

    expect(html).toContain('class="panel-scrim"');
    expect(html).toContain('aria-label="Close status"');
    expect(html).toContain('aria-label="System status"');
    expect(html).toContain('>Server<');
    expect(html).toContain('>Radar<');
    expect(html).toContain('>Connected<');
    expect(html).toContain('>Disconnected<');
  });
});

import { describe, expect, it } from 'vitest';
import { renderToString } from 'react-dom/server';
import { StatusMenu } from './StatusMenu';

describe('StatusMenu', () => {
  it('states server, radar, and ball detection in words', () => {
    const html = renderToString(
      <StatusMenu connected radarConnected={false} ballDetection="Searching" onClose={() => {}} />
    );

    expect(html).toContain('class="panel-scrim"');
    expect(html).toContain('aria-label="Close status"');
    expect(html).toContain('aria-label="System status"');
    expect(html).toContain('>Server<');
    expect(html).toContain('>Radar<');
    expect(html).toContain('>Ball detection<');
    expect(html).toContain('>Connected<');
    expect(html).toContain('>Disconnected<');
    expect(html).toContain('>Searching<');
    expect(html).not.toContain('GSPro');
    expect(html).not.toContain('sim-status');
  });

  it('lists simulator connector states when sim_status has been received', () => {
    const html = renderToString(
      <StatusMenu
        connected
        radarConnected
        ballDetection="Off"
        simStatuses={{
          gspro: { target: 'gspro', state: 'reconnecting', attempt: 2, next_retry_in_s: 4 },
          opengolfsim: { target: 'opengolfsim', state: 'error', message: 'Connection refused' },
        }}
        onClose={() => {}}
      />
    );

    expect(html).toContain('>Simulators<');
    expect(html).toContain('GSPro');
    expect(html).toContain('reconnecting');
    expect(html).toContain('sim-status__pill--warn');
    expect(html).toContain('OpenGolfSim');
    expect(html).toContain('sim-status__pill--error');
  });
});

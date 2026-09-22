import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClearSessionDialog } from './ClearSessionDialog';

const noop = () => {};

describe('ClearSessionDialog', () => {
  it('asks before clearing and names the profile', () => {
    const html = renderToString(<ClearSessionDialog profileName="Alex" onConfirm={noop} onCancel={noop} />);

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('id="clear-session-title"');
    expect(html).toContain('Clear Alex&#x27;s session?');
    expect(html).toContain('This removes Alex&#x27;s shots. Other profiles are kept.');
    expect(html).toContain('>Clear session<');
    expect(html).toContain('>Cancel<');
    expect(html).toContain('clear-session-modal');
    expect(html).toContain('panel-action--danger');
  });

  it('keeps the confirm control as Clear session, matching the header action', () => {
    const html = renderToString(<ClearSessionDialog profileName="Profile 1" onConfirm={noop} onCancel={noop} />);

    expect(html).toContain('Clear Profile 1&#x27;s session?');
    expect(html).toMatch(/panel-action--danger[^>]*>Clear session</);
  });
});

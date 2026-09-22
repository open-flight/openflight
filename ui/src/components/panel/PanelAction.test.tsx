import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PanelAction } from './PanelAction';

describe('PanelAction', () => {
  it('defaults to the primary variant', () => {
    const html = renderToString(<PanelAction>Change club</PanelAction>);

    expect(html).toContain('panel-action panel-action--primary');
    expect(html).toContain('>Change club<');
  });

  it('renders secondary and danger variants', () => {
    expect(renderToString(<PanelAction variant="secondary">Record</PanelAction>)).toContain('panel-action--secondary');
    expect(renderToString(<PanelAction variant="danger">Clear session</PanelAction>)).toContain('panel-action--danger');
  });
});

import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { afterEach, describe, expect, it } from 'vitest';
import { setActiveLocale } from '../../i18n';
import { MetricCard } from './MetricCard';

const css = readFileSync(fileURLToPath(new URL('./MetricCard.css', import.meta.url)), 'utf8');

describe('MetricCard', () => {
  afterEach(() => {
    setActiveLocale('en');
  });

  it('renders value, unit, and label', () => {
    const html = renderToString(<MetricCard value="92.0" unit="mph" label="Ball Speed" />);
    expect(html).toContain('92.0');
    expect(html).toContain('mph');
    expect(html).toContain('Ball Speed');
    expect(html).toContain('metric-card--default');
  });

  it('marks emphasis and hero variants', () => {
    const html = renderToString(
      <MetricCard value="133" unit="yds" label="Est. Carry" variant="emphasis" size="hero" />
    );
    expect(html).toContain('metric-card--emphasis');
    expect(html).toContain('metric-card--hero');
  });

  it('marks experimental with an icon instead of caption text', () => {
    const html = renderToString(<MetricCard value="8,750" unit="rpm" label="Spin Rate" confidence="experimental" />);
    expect(html).toContain('metric-card__experimental');
    expect(html).toContain('metric-card__experimental-label">Experimental<');
    expect(html).not.toContain('metric-card__confidence-dots');
    expect(html).not.toContain('metric-card__confidence--experimental');
    expect(html).not.toMatch(/metric-card__confidence-label">experimental</i);
  });

  it('keeps measured confidence dots while marking camera-fused data experimental', () => {
    const html = renderToString(<MetricCard value="3.1" unit="°" label="Club path" confidence="high" experimental />);

    expect(html).toContain('metric-card__confidence--high');
    expect(html).toContain('metric-card__confidence-dots');
    expect(html).toContain('metric-card__experimental');
    expect(html).not.toMatch(/metric-card__confidence-label">experimental</i);
  });

  it('translates the experimental icon label', () => {
    setActiveLocale('fr');
    const html = renderToString(<MetricCard value="8,750" unit="rpm" label="Spin Rate" experimental />);
    expect(html).toContain('metric-card__experimental-label">Expérimental<');
  });

  it('shows an estimated mark on the title, not beside the value', () => {
    const estimated = renderToString(
      <MetricCard value="8.9" unit="°" label="V. launch" labelPosition="above" estimated />
    );
    const labelIdx = estimated.indexOf('metric-card__label');
    const markIdx = estimated.indexOf('metric-card__estimated');
    const valueIdx = estimated.indexOf('metric-card__value-row');
    expect(markIdx).toBeGreaterThan(labelIdx);
    expect(markIdx).toBeLessThan(valueIdx);

    const measured = renderToString(<MetricCard value="140.7" unit="mph" label="Ball speed" />);
    expect(measured).not.toContain('metric-card__estimated');
  });

  it('always reserves a meta slot on label-above tiles so values share a row', () => {
    const empty = renderToString(<MetricCard value="-2.9" unit="°" label="Club path" labelPosition="above" />);
    const dense = renderToString(
      <MetricCard value="8.9" unit="°" label="V. launch" labelPosition="above" subtext="estimated" confidence="high" />
    );

    expect(empty).toContain('metric-card__meta');
    expect(dense).toContain('metric-card__meta');
  });

  it('vertically centers label-above content on shared label/value/meta bands', () => {
    expect(css).toMatch(/\.metric-card--label-above \{[^}]*grid-template-rows: 1fr auto auto auto 1fr/);
    expect(css).toMatch(/\.metric-card--label-above \.metric-card__label \{[^}]*grid-row: 2/);
    expect(css).toMatch(/\.metric-card--label-above \.metric-card__label \{[^}]*min-height: 16px/);
    expect(css).toMatch(/\.metric-card--label-above \.metric-card__value-row \{[^}]*grid-row: 3/);
    expect(css).toMatch(/\.metric-card--label-above \.metric-card__meta \{[^}]*grid-row: 4/);
    expect(css).toMatch(/\.metric-card--label-above \.metric-card__meta \{[^}]*min-height: 1\.125rem/);
    expect(css).toMatch(/\.metric-card__unit \{[^}]*line-height: 1/);
  });

  it('sets subtext in the same caption type as spin accuracy', () => {
    const html = renderToString(
      <MetricCard value="+3.4" unit="°" label="Spin axis" labelPosition="above" subtext="Fade" />
    );
    expect(html).toMatch(/metric-card__subtext metric-card__confidence-label[^>]*>Fade</);
  });

  it('does not paint the title yellow from :hover', () => {
    // Selecting a tile reorders the grid. Touch (and the compatibility mouse
    // events Windows synthesizes after a tap) leave :hover at the tap point,
    // which is then occupied by the previous hero. Hover media queries still
    // match on hybrid PCs, so the title color must not use :hover at all.
    expect(css).not.toMatch(/:hover[^\n]*metric-card__label/);
    expect(css).toMatch(/\.metric-card--selected \.metric-card__label \{[^}]*color: var\(--color-accent\)/);
  });

  it('keeps values and units on full text color', () => {
    expect(css).toMatch(/\.metric-card__value \{[^}]*color: var\(--color-text\)/);
    expect(css).toMatch(/\.metric-card__unit \{[^}]*color: var\(--color-text\)/);
    expect(css).not.toMatch(/\.metric-card__unit,\s*\.metric-card__label \{[^}]*--color-text-muted/);
  });

  it('does not draw a full accent box around the selected tile', () => {
    expect(css).not.toMatch(/\.metric-card--selected \{[^}]*inset 0 0 0 2px var\(--color-accent\)/);
  });
});

import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { PickerOverlay } from './PickerOverlay';
import { clubSections } from './pickerSections';

describe('PickerOverlay', () => {
  it('opens the Woods tab for the default driver so DR is the selected tile', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="driver"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).toContain('aria-label="Groups"');
    expect(html).toMatch(/panel-action--primary[^>]*>Woods</);
    expect(html).toMatch(/panel-action--secondary[^>]*>Irons</);
    expect(html).toMatch(/panel-action--secondary[^>]*>Hybrids</);
    expect(html).toMatch(/picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*>DR</);
    expect(html).toContain('--picker-rows:3');
    expect(html).not.toContain('>7i<');
    expect(html).toContain('>3W<');
  });

  it('opens the Irons tab when a mid-iron is already selected', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="7-iron"
        sections={clubSections()}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).toMatch(/panel-action--primary[^>]*>Irons</);
    expect(html).toMatch(/picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*>7i</);
    expect(html).not.toContain('>DR<');
  });
});

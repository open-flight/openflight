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

  it('shows each bag club with its loft under the label, and the model below', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="b"
        sections={[
          {
            name: 'Irons',
            options: [
              { id: 'a', label: 'SW', detail: '56°', note: 'Spinner' },
              { id: 'b', label: 'SW', detail: '56°', note: 'Digger' },
            ],
          },
        ]}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).toMatch(
      /picker-overlay__option-label">SW<\/span><span class="picker-overlay__option-detail">56°<\/span><span class="picker-overlay__option-note">Spinner</
    );
    expect(html).toMatch(
      /picker-overlay__option--selected[^>]*aria-pressed="true"[^>]*><span[^>]*>SW<\/span><span[^>]*>56°<\/span><span[^>]*>Digger</
    );
  });

  it('omits the extra lines when a tile has no loft or model', () => {
    const html = renderToString(
      <PickerOverlay
        title="Select club"
        selectedId="pw"
        sections={[{ name: 'Irons', options: [{ id: 'pw', label: 'PW', detail: '', note: '' }] }]}
        onSelect={() => {}}
        onClose={() => {}}
      />
    );

    expect(html).toMatch(/aria-pressed="true"[^>]*>PW</);
    expect(html).not.toContain('picker-overlay__option-detail');
  });
});

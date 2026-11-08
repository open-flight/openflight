import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { MAX_BAG_CLUBS, type CustomClub } from '../../data/bag';
import { BagEditor, BagInfo } from './BagEditor';

/** React SSR splits interpolated text with comment markers; drop them. */
function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function wedge(id: string, overrides: Partial<CustomClub> = {}): CustomClub {
  return {
    id,
    bag: 'home',
    club: 'sw',
    name: 'Sand Wedge',
    label: 'SW',
    brand: 'Acme',
    model: 'Spinner',
    loft_deg: 56,
    bounce_deg: 10,
    lie_deg: null,
    shaft: '',
    notes: '',
    hand: null,
    ...overrides,
  };
}

function render(overrides: Partial<Parameters<typeof BagEditor>[0]> = {}) {
  return text(
    renderToString(
      <BagEditor
        profileName="Home"
        bag={[]}
        defaultLofts={{ sw: 54 }}
        error={null}
        onSave={() => {}}
        onRemove={() => {}}
        onClose={() => {}}
        {...overrides}
      />
    )
  );
}

describe('BagEditor', () => {
  it('titles itself with the profile name', () => {
    const html = render();

    expect(html).toContain('aria-label="Bag · Home"');
    expect(html).toContain('role="dialog"');
  });

  it('explains the empty bag', () => {
    const html = render();

    expect(html).toContain('No clubs in this bag yet');
    expect(html).toContain('every club is offered');
  });

  it('lists two wedges of the same loft with their brand, model and specs', () => {
    const html = render({ bag: [wedge('a'), wedge('b', { brand: 'Other', model: 'Digger', bounce_deg: 14 })] });

    expect(html).toContain('Acme Spinner');
    expect(html).toContain('Other Digger');
    expect(html).toContain('56° loft · 10° bounce');
    expect(html).toContain('56° loft · 14° bounce');
    expect(html.match(/bag-editor__club">SW</g)).toHaveLength(2);
  });

  it('adds the hand to the specs line when recorded', () => {
    const html = render({ bag: [wedge('a', { hand: 'left' }), wedge('b', { hand: 'right' })] });

    expect(html).toContain('56° loft · 10° bounce · Left-handed');
    expect(html).toContain('56° loft · 10° bounce · Right-handed');
  });

  it('labels each row for editing and removal', () => {
    const html = render({ bag: [wedge('a')] });

    expect(html).toContain('aria-label="Edit Sand Wedge 56° Acme Spinner"');
    expect(html).toContain('aria-label="Remove Acme Spinner"');
  });

  it('falls back to the club name, and shows the inherited loft, when nothing else is recorded', () => {
    const html = render({ bag: [wedge('a', { brand: '', model: '', loft_deg: 54, bounce_deg: null })] });

    expect(html).toContain('bag-editor__name">Sand Wedge<');
    expect(html).toContain('bag-editor__specs">54° loft<');
  });

  it('shows a server rejection', () => {
    const html = render({ error: 'loft_deg must be between 0 and 80 degrees' });

    expect(html).toContain('role="alert"');
    // SSR escapes the apostrophe in "Couldn't".
    expect(html).toContain('Couldn&#x27;t save the bag: loft_deg must be between 0 and 80 degrees');
  });

  it('offers a closed info toggle next to the close button', () => {
    const html = render({ bag: [wedge('a')] });

    expect(html).toMatch(/<button[^>]*aria-label="What these values do"[^>]*aria-expanded="false"[^>]*>i</);
    expect(html).toContain('Acme Spinner');
  });

  it('explains what changes the numbers, what keeps shots apart and what annotates them', () => {
    const html = text(renderToString(<BagInfo />));

    expect(html).toContain('Club: changes the numbers');
    expect(html).toContain('Each club in the bag: keeps your shots apart');
    expect(html).toContain('two sand wedges separately');
    expect(html).toContain('Loft, brand, model, bounce, lie, shaft, hand and notes: annotate your shots');
    expect(html).toContain('They don&#x27;t change the numbers.');
  });

  it('offers Add club until the bag is full', () => {
    expect(render({ bag: [wedge('a')] })).toMatch(/<button[^>]*panel-action--primary[^>]*>Add club</);

    const full = render({ bag: Array.from({ length: MAX_BAG_CLUBS }, (_, n) => wedge(String(n))) });
    const addButton = full.match(/<button[^>]*panel-action--primary[^>]*>[\s\S]*?<\/button>/)?.[0] ?? '';

    expect(addButton).toContain('disabled=""');
    expect(addButton).toContain(`Bag full (${MAX_BAG_CLUBS})`);
  });
});

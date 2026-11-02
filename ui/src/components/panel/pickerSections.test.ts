import { describe, expect, it } from 'vitest';
import { ALL_CLUBS } from '../../data/clubs';
import type { BagClub } from '../../data/bag';
import {
  OTHER_CLUBS_SECTION,
  OTHER_CLUB_PREFIX,
  bagClubSections,
  bagPickerSelectedId,
  clubSections,
  initialPickerSection,
  pickerGridRows,
  resolveBagPickerOption,
  trainingImplementSections,
} from './pickerSections';

describe('initialPickerSection', () => {
  const clubs = clubSections();

  it('opens Woods when the driver is selected', () => {
    expect(initialPickerSection(clubs, 'driver')).toBe('Woods');
  });

  it('opens Irons when a wedge is selected', () => {
    expect(initialPickerSection(clubs, 'pw')).toBe('Irons');
  });

  it('falls back to the first section when the id is unknown', () => {
    expect(initialPickerSection(clubs, 'not-a-club')).toBe('Irons');
  });

  it('opens the matching training group', () => {
    const groups = trainingImplementSections();
    expect(initialPickerSection(groups, 'stack-160g')).toBe('TheStack');
  });
});

describe('pickerGridRows', () => {
  it('uses three rows for the iron set so woods tiles match that cube size', () => {
    expect(pickerGridRows(clubSections())).toBe(3);
  });

  it('uses the densest training group so every tab fits', () => {
    expect(pickerGridRows(trainingImplementSections())).toBe(4);
  });
});

function bagEntry(id: string, type: string, extra: Partial<BagClub> = {}): BagClub {
  return {
    id,
    club: type,
    brand: '',
    model: '',
    loft_deg: null,
    bounce_deg: null,
    lie_deg: null,
    shaft: '',
    notes: '',
    hand: null,
    ...extra,
  };
}

describe('bagClubSections', () => {
  it('shows two wedges of the same loft as separate tiles with loft and model', () => {
    const sections = bagClubSections([
      bagEntry('a', 'sw', { loft_deg: 56, model: 'Spinner' }),
      bagEntry('b', 'sw', { loft_deg: 56, brand: 'Other' }),
    ]);

    expect(sections[0]).toEqual({
      name: 'Irons',
      options: [
        { id: 'a', label: 'SW', detail: '56°', note: 'Spinner' },
        { id: 'b', label: 'SW', detail: '56°', note: 'Other' },
      ],
    });
  });

  it('orders by the built-in club order, keeping bag order within a type', () => {
    const sections = bagClubSections([
      bagEntry('lw', 'lw'),
      bagEntry('pw', 'pw'),
      bagEntry('sw-2', 'sw'),
      bagEntry('sw-1', 'sw'),
    ]);

    expect(sections[0].options.map((option) => option.id)).toEqual(['pw', 'sw-2', 'sw-1', 'lw']);
  });

  it('leaves out families the bag does not carry', () => {
    const names = bagClubSections([bagEntry('dr', 'driver'), bagEntry('pw', 'pw')]).map((section) => section.name);

    expect(names).toEqual(['Irons', 'Woods', OTHER_CLUBS_SECTION]);
  });

  it('offers every club the bag lacks under Other, by type id', () => {
    const other = bagClubSections([bagEntry('pw', 'pw')]).find((section) => section.name === OTHER_CLUBS_SECTION);
    const ids = other?.options.map((option) => option.id) ?? [];

    expect(ids).toHaveLength(ALL_CLUBS.length - 1);
    expect(ids).toContain(`${OTHER_CLUB_PREFIX}driver`);
    expect(ids).not.toContain(`${OTHER_CLUB_PREFIX}pw`);
  });

  it('drops Other once the bag carries every club type', () => {
    const sections = bagClubSections(ALL_CLUBS.map((entry) => bagEntry(entry.id, entry.id)));

    expect(sections.map((section) => section.name)).not.toContain(OTHER_CLUBS_SECTION);
  });

  it('leaves detail and note blank when nothing is recorded', () => {
    expect(bagClubSections([bagEntry('pw', 'pw')])[0].options[0]).toEqual({
      id: 'pw',
      label: 'PW',
      detail: '',
      note: '',
    });
  });
});

describe('bag picker selection', () => {
  const bag = [bagEntry('sw-a', 'sw', { loft_deg: 56 })];

  it('resolves a bag tile to its club and id', () => {
    expect(resolveBagPickerOption(bag, 'sw-a')).toEqual({ club: 'sw', bagClubId: 'sw-a' });
  });

  it('resolves an Other tile to a plain club type', () => {
    expect(resolveBagPickerOption(bag, `${OTHER_CLUB_PREFIX}driver`)).toEqual({ club: 'driver', bagClubId: '' });
  });

  it('rejects ids the bag no longer has, and unknown types', () => {
    expect(resolveBagPickerOption(bag, 'gone')).toBeNull();
    expect(resolveBagPickerOption(bag, `${OTHER_CLUB_PREFIX}putter`)).toBeNull();
  });

  it('highlights the bag club, or the Other tile for a plain type', () => {
    expect(bagPickerSelectedId('sw', 'sw-a')).toBe('sw-a');
    expect(bagPickerSelectedId('driver', '')).toBe(`${OTHER_CLUB_PREFIX}driver`);
  });

  it('opens on the tab holding the selection', () => {
    const sections = bagClubSections(bag);

    expect(initialPickerSection(sections, bagPickerSelectedId('sw', 'sw-a'))).toBe('Irons');
    expect(initialPickerSection(sections, bagPickerSelectedId('driver', ''))).toBe(OTHER_CLUBS_SECTION);
  });
});

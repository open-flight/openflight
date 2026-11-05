import { describe, expect, it } from 'vitest';
import {
  bagClubs,
  clubShortLabel,
  customClubCompactLabel,
  customClubDisplayName,
  customClubMakeModel,
  customClubSpecs,
  findCustomClub,
  formatDegrees,
  parseClubsSnapshot,
  type CustomClub,
} from './bag';

function customClub(overrides: Partial<CustomClub> = {}): CustomClub {
  return {
    id: 'sw-a',
    bag: 'home',
    club: 'sw',
    name: 'Sand Wedge',
    label: 'SW',
    loft_deg: 56,
    brand: 'Acme',
    model: 'Spinner',
    bounce_deg: 10,
    lie_deg: null,
    shaft: '',
    hand: null,
    notes: '',
    ...overrides,
  };
}

const SPEC_LABELS = {
  loft_deg: 'loft',
  bounce_deg: 'bounce',
  lie_deg: 'lie',
  right: 'Right-handed',
  left: 'Left-handed',
};

describe('parseClubsSnapshot', () => {
  it('reads clubs and the inherited lofts', () => {
    const snapshot = parseClubsSnapshot({
      version: 1,
      clubs: [customClub(), customClub({ id: 'sw-b', model: 'Digger', bounce_deg: 14 })],
      lofts: { sw: 54, driver: 10.5 },
    });

    expect(snapshot.clubs.map((club) => club.id)).toEqual(['sw-a', 'sw-b']);
    expect(snapshot.clubs[1].bounce_deg).toBe(14);
    expect(snapshot.lofts).toEqual({ sw: 54, driver: 10.5 });
  });

  it('is empty for anything that is not a snapshot', () => {
    for (const raw of [null, undefined, 'clubs', [], { clubs: 'x', lofts: 'y' }]) {
      expect(parseClubsSnapshot(raw)).toEqual({ clubs: [], lofts: {} });
    }
  });

  it('skips clubs without an id, a bag, a known type or a loft', () => {
    const { clubs } = parseClubsSnapshot({
      clubs: [
        customClub({ id: '' }),
        customClub({ id: 'no-bag', bag: '' }),
        customClub({ id: 'putter', club: 'putter' }),
        { ...customClub({ id: 'no-loft' }), loft_deg: null },
        null,
        'sw',
        customClub({ id: 'ok' }),
      ],
    });

    expect(clubs.map((club) => club.id)).toEqual(['ok']);
  });

  it('fills missing or mistyped fields, inheriting the name and label', () => {
    const { clubs } = parseClubsSnapshot({
      clubs: [{ id: 'a', bag: 'home', club: 'pw', loft_deg: 46, brand: 7, hand: 'both' }],
    });

    expect(clubs[0]).toEqual({
      id: 'a',
      bag: 'home',
      club: 'pw',
      name: 'Pitching Wedge',
      label: 'PW',
      loft_deg: 46,
      brand: '',
      model: '',
      bounce_deg: null,
      lie_deg: null,
      shaft: '',
      hand: null,
      notes: '',
    });
  });

  it('keeps only lofts for known club types', () => {
    expect(parseClubsSnapshot({ lofts: { sw: 54, putter: 3, lw: 'x' } }).lofts).toEqual({ sw: 54 });
  });
});

describe('bagClubs and findCustomClub', () => {
  const clubs = [customClub(), customClub({ id: 'other', bag: 'range' }), customClub({ id: 'sw-b' })];

  it('picks one bag in order', () => {
    expect(bagClubs(clubs, 'home').map((club) => club.id)).toEqual(['sw-a', 'sw-b']);
    expect(bagClubs(clubs, '')).toEqual([]);
  });

  it('finds by id and misses cleanly', () => {
    expect(findCustomClub(clubs, 'sw-b')?.id).toBe('sw-b');
    expect(findCustomClub(clubs, 'missing')).toBeNull();
    expect(findCustomClub(clubs, '')).toBeNull();
  });
});

describe('formatDegrees', () => {
  it('drops a trailing .0 and keeps one decimal', () => {
    expect(formatDegrees(56)).toBe('56°');
    expect(formatDegrees(56.5)).toBe('56.5°');
    expect(formatDegrees(-2)).toBe('-2°');
    expect(formatDegrees(0)).toBe('0°');
  });

  it('is blank when not recorded', () => {
    expect(formatDegrees(null)).toBe('');
    expect(formatDegrees(undefined)).toBe('');
    expect(formatDegrees(Number.NaN)).toBe('');
  });
});

describe('custom club labels', () => {
  it('uses the tile label for the club type', () => {
    expect(clubShortLabel('sw')).toBe('SW');
    expect(clubShortLabel('7-iron')).toBe('7i');
    expect(clubShortLabel('mystery')).toBe('MYSTERY');
  });

  it('joins brand and model', () => {
    expect(customClubMakeModel(customClub())).toBe('Acme Spinner');
    expect(customClubMakeModel(customClub({ brand: '' }))).toBe('Spinner');
    expect(customClubMakeModel(customClub({ brand: '', model: '' }))).toBe('');
  });

  it('names the club with loft and model for the header', () => {
    expect(customClubDisplayName(customClub())).toBe('Sand Wedge · 56° · Spinner');
    expect(customClubDisplayName(customClub({ model: '' }))).toBe('Sand Wedge · 56° · Acme');
    expect(customClubDisplayName(customClub({ model: '', brand: '' }))).toBe('Sand Wedge · 56°');
  });

  it('builds a compact label that tells two 56° wedges apart', () => {
    expect(customClubCompactLabel(customClub())).toBe('SW 56° Spinner');
    expect(customClubCompactLabel(customClub({ id: 'b', model: 'Digger' }))).toBe('SW 56° Digger');
  });

  it('lists recorded specs in loft, bounce, lie order, then the hand', () => {
    expect(customClubSpecs(customClub({ lie_deg: 64 }), SPEC_LABELS)).toBe('56° loft · 10° bounce · 64° lie');
    expect(customClubSpecs(customClub({ bounce_deg: null }), SPEC_LABELS)).toBe('56° loft');
    expect(customClubSpecs(customClub({ hand: 'left' }), SPEC_LABELS)).toBe('56° loft · 10° bounce · Left-handed');
  });
});

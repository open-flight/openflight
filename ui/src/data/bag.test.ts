import { describe, expect, it } from 'vitest';
import type { Profile } from '../types/profile';
import {
  bagClubCompactLabel,
  bagClubDisplayName,
  bagClubMakeModel,
  bagClubSpecs,
  clubShortLabel,
  findBagClub,
  formatDegrees,
  profileBag,
  type BagClub,
} from './bag';

function profileWith(bag: unknown): Profile {
  return { id: 'p', name: 'Home', created_at: '2026-11-01T00:00:00Z', settings: { bag } };
}

function bagClub(overrides: Partial<BagClub> = {}): BagClub {
  return {
    id: 'sw-a',
    club: 'sw',
    brand: 'Acme',
    model: 'Spinner',
    loft_deg: 56,
    bounce_deg: 10,
    lie_deg: null,
    shaft: '',
    notes: '',
    ...overrides,
  };
}

const SPEC_LABELS = { loft_deg: 'loft', bounce_deg: 'bounce', lie_deg: 'lie' };

describe('profileBag', () => {
  it('is empty for a missing profile or a profile without a bag', () => {
    expect(profileBag(null)).toEqual([]);
    expect(profileBag(undefined)).toEqual([]);
    expect(profileBag({ id: 'p', name: 'Home', created_at: '', settings: {} })).toEqual([]);
  });

  it('is empty when the bag is not a list', () => {
    expect(profileBag(profileWith({ sw: true }))).toEqual([]);
    expect(profileBag(profileWith('sw'))).toEqual([]);
  });

  it('keeps two clubs of the same type in order', () => {
    const bag = profileBag(profileWith([bagClub(), bagClub({ id: 'sw-b', model: 'Digger', bounce_deg: 14 })]));

    expect(bag.map((club) => club.id)).toEqual(['sw-a', 'sw-b']);
    expect(bag[1].bounce_deg).toBe(14);
  });

  it('skips entries without an id or with an unknown club', () => {
    const bag = profileBag(
      profileWith([bagClub({ id: '' }), bagClub({ id: 'x', club: 'putter' }), null, 'sw', bagClub({ id: 'ok' })])
    );

    expect(bag.map((club) => club.id)).toEqual(['ok']);
  });

  it('fills missing or mistyped fields with blanks', () => {
    const [club] = profileBag(profileWith([{ id: 'a', club: 'pw', loft_deg: '46', brand: 7 }]));

    expect(club).toEqual({
      id: 'a',
      club: 'pw',
      brand: '',
      model: '',
      loft_deg: null,
      bounce_deg: null,
      lie_deg: null,
      shaft: '',
      notes: '',
    });
  });
});

describe('findBagClub', () => {
  it('finds by id and misses cleanly', () => {
    const bag = [bagClub(), bagClub({ id: 'sw-b' })];

    expect(findBagClub(bag, 'sw-b')?.id).toBe('sw-b');
    expect(findBagClub(bag, 'missing')).toBeNull();
    expect(findBagClub(bag, '')).toBeNull();
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

describe('bag club labels', () => {
  it('uses the tile label for the club type', () => {
    expect(clubShortLabel('sw')).toBe('SW');
    expect(clubShortLabel('7-iron')).toBe('7i');
    expect(clubShortLabel('mystery')).toBe('MYSTERY');
  });

  it('joins brand and model', () => {
    expect(bagClubMakeModel(bagClub())).toBe('Acme Spinner');
    expect(bagClubMakeModel(bagClub({ brand: '' }))).toBe('Spinner');
    expect(bagClubMakeModel(bagClub({ brand: '', model: '' }))).toBe('');
  });

  it('names the club with loft and model for the header', () => {
    expect(bagClubDisplayName(bagClub())).toBe('Sand Wedge · 56° · Spinner');
  });

  it('falls back to the brand, and leaves out what is not recorded', () => {
    expect(bagClubDisplayName(bagClub({ model: '' }))).toBe('Sand Wedge · 56° · Acme');
    expect(bagClubDisplayName(bagClub({ model: '', brand: '', loft_deg: null }))).toBe('Sand Wedge');
  });

  it('builds a compact label that tells two 56° wedges apart', () => {
    expect(bagClubCompactLabel(bagClub())).toBe('SW 56° Spinner');
    expect(bagClubCompactLabel(bagClub({ id: 'b', model: 'Digger' }))).toBe('SW 56° Digger');
  });

  it('lists recorded specs in loft, bounce, lie order', () => {
    expect(bagClubSpecs(bagClub({ lie_deg: 64 }), SPEC_LABELS)).toBe('56° loft · 10° bounce · 64° lie');
    expect(bagClubSpecs(bagClub({ loft_deg: null }), SPEC_LABELS)).toBe('10° bounce');
    expect(bagClubSpecs(bagClub({ loft_deg: null, bounce_deg: null }), SPEC_LABELS)).toBe('');
  });
});

import { describe, expect, it } from 'vitest';
import { BAG_NOTES_MAX, BAG_TEXT_MAX, type BagClub } from '../../data/bag';
import {
  bagClubFromDraft,
  draftErrors,
  draftFromBagClub,
  fieldMaxLength,
  isAngleField,
  newBagClubDraft,
  newBagClubId,
  parseAngle,
  removeBagClub,
  upsertBagClub,
} from './bagDraft';

const WEDGE: BagClub = {
  id: 'sw-a',
  club: 'sw',
  brand: 'Acme',
  model: 'Spinner',
  loft_deg: 56,
  bounce_deg: 10,
  lie_deg: null,
  shaft: 'Stiff',
  notes: '',
  hand: 'left',
};

describe('parseAngle', () => {
  it('treats blank as not recorded', () => {
    expect(parseAngle('', 'loft_deg')).toEqual({ value: null });
    expect(parseAngle('   ', 'loft_deg')).toEqual({ value: null });
  });

  it('accepts plain decimals and rounds to 0.1', () => {
    expect(parseAngle('56', 'loft_deg')).toEqual({ value: 56 });
    expect(parseAngle('56.04', 'loft_deg')).toEqual({ value: 56 });
    expect(parseAngle(' 8.06 ', 'bounce_deg')).toEqual({ value: 8.1 });
    expect(parseAngle('.5', 'bounce_deg')).toEqual({ value: 0.5 });
    expect(parseAngle('56.', 'loft_deg')).toEqual({ value: 56 });
  });

  it('allows negative bounce', () => {
    expect(parseAngle('-2', 'bounce_deg')).toEqual({ value: -2 });
  });

  it.each(['fifty', '5,6', '1e1', '56°', '--2', '.', '-', "5'6"])('rejects %s as not a number', (raw) => {
    expect(parseAngle(raw, 'loft_deg')).toEqual({ error: 'number' });
  });

  it.each([
    ['loft_deg', '560'],
    ['loft_deg', '-1'],
    ['bounce_deg', '31'],
    ['bounce_deg', '-10.5'],
    ['lie_deg', '39'],
    ['lie_deg', '81'],
  ] as const)('rejects %s %s as out of range', (field, raw) => {
    expect(parseAngle(raw, field)).toEqual({ error: 'range' });
  });

  it.each([
    ['loft_deg', '0', 0],
    ['loft_deg', '80', 80],
    ['bounce_deg', '-10', -10],
    ['lie_deg', '40', 40],
  ] as const)('includes the %s bound %s', (field, raw, value) => {
    expect(parseAngle(raw, field)).toEqual({ value });
  });
});

describe('drafts', () => {
  it('starts a new club blank, with no id yet', () => {
    expect(newBagClubDraft('sw')).toEqual({
      id: '',
      club: 'sw',
      hand: '',
      brand: '',
      model: '',
      loft_deg: '',
      bounce_deg: '',
      lie_deg: '',
      shaft: '',
      notes: '',
    });
  });

  it('round-trips an existing club through a draft unchanged', () => {
    const draft = draftFromBagClub(WEDGE);

    expect(draft.loft_deg).toBe('56');
    expect(draft.lie_deg).toBe('');
    expect(bagClubFromDraft(draft)).toEqual(WEDGE);
  });

  it('carries the hand through a draft, and saves a blank hand as not recorded', () => {
    expect(draftFromBagClub(WEDGE).hand).toBe('left');
    expect(draftFromBagClub({ ...WEDGE, hand: null }).hand).toBe('');
    expect(bagClubFromDraft({ ...newBagClubDraft('sw'), hand: 'right' })?.hand).toBe('right');
    expect(bagClubFromDraft(newBagClubDraft('sw'))?.hand).toBeNull();
  });

  it('reports each invalid angle', () => {
    const draft = { ...newBagClubDraft('sw'), loft_deg: '560', bounce_deg: 'x' };

    expect(draftErrors(draft)).toEqual({ loft_deg: 'range', bounce_deg: 'number' });
    expect(bagClubFromDraft(draft)).toBeNull();
  });

  it('has no errors for a blank draft', () => {
    expect(draftErrors(newBagClubDraft('pw'))).toEqual({});
  });

  it('gives a new club an id and tidies its text', () => {
    const club = bagClubFromDraft({ ...newBagClubDraft('lw'), brand: '  Acme   Golf ', loft_deg: '60' });

    expect(club?.id).toMatch(/^[0-9a-z]{8,}$/);
    expect(club?.brand).toBe('Acme Golf');
    expect(club?.loft_deg).toBe(60);
    expect(club?.bounce_deg).toBeNull();
  });

  it('caps text at the server limits', () => {
    const club = bagClubFromDraft({
      ...newBagClubDraft('lw'),
      model: 'M'.repeat(BAG_TEXT_MAX + 5),
      notes: 'n'.repeat(BAG_NOTES_MAX + 5),
    });

    expect(club?.model).toHaveLength(BAG_TEXT_MAX);
    expect(club?.notes).toHaveLength(BAG_NOTES_MAX);
  });
});

describe('field metadata', () => {
  it('knows which fields are angles', () => {
    expect(isAngleField('loft_deg')).toBe(true);
    expect(isAngleField('bounce_deg')).toBe(true);
    expect(isAngleField('lie_deg')).toBe(true);
    expect(isAngleField('brand')).toBe(false);
    expect(isAngleField('notes')).toBe(false);
  });

  it('caps each field to the right length', () => {
    expect(fieldMaxLength('brand')).toBe(BAG_TEXT_MAX);
    expect(fieldMaxLength('notes')).toBe(BAG_NOTES_MAX);
    expect(fieldMaxLength('loft_deg')).toBe(6);
  });
});

describe('newBagClubId', () => {
  it('is unique across quick successive calls', () => {
    const ids = new Set(Array.from({ length: 200 }, () => newBagClubId()));

    expect(ids.size).toBe(200);
  });
});

describe('bag list edits', () => {
  const other: BagClub = { ...WEDGE, id: 'sw-b', model: 'Digger' };

  it('appends a new club', () => {
    expect(upsertBagClub([WEDGE], other).map((club) => club.id)).toEqual(['sw-a', 'sw-b']);
  });

  it('replaces an edited club in place', () => {
    const edited = { ...WEDGE, bounce_deg: 12 };

    const bag = upsertBagClub([WEDGE, other], edited);

    expect(bag.map((club) => club.id)).toEqual(['sw-a', 'sw-b']);
    expect(bag[0].bounce_deg).toBe(12);
  });

  it('removes only the named club', () => {
    expect(removeBagClub([WEDGE, other], 'sw-a')).toEqual([other]);
    expect(removeBagClub([WEDGE], 'missing')).toEqual([WEDGE]);
  });

  it('never mutates the input bag', () => {
    const bag = [WEDGE];

    upsertBagClub(bag, other);
    removeBagClub(bag, 'sw-a');

    expect(bag).toEqual([WEDGE]);
  });
});

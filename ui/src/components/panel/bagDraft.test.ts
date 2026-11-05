import { describe, expect, it } from 'vitest';
import { BAG_NOTES_MAX, BAG_TEXT_MAX, type CustomClub } from '../../data/bag';
import {
  draftErrors,
  draftFromCustomClub,
  fieldMaxLength,
  isAngleField,
  newBagClubDraft,
  parseAngle,
  payloadFromDraft,
} from './bagDraft';

const WEDGE: CustomClub = {
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
  shaft: 'Stiff',
  hand: 'left',
  notes: '',
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
  it('starts a new club blank, with no id', () => {
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

  it('round-trips an existing club, keeping its id', () => {
    const draft = draftFromCustomClub(WEDGE);

    expect(draft.loft_deg).toBe('56');
    expect(draft.lie_deg).toBe('');
    expect(draft.hand).toBe('left');
    expect(payloadFromDraft(draft)).toEqual({
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
    });
  });

  it('sends no id for a new club: the server issues it', () => {
    expect(payloadFromDraft(newBagClubDraft('lw'))).not.toHaveProperty('id');
  });

  it('sends a blank loft as null so the club inherits its type loft', () => {
    expect(payloadFromDraft(newBagClubDraft('lw'))?.loft_deg).toBeNull();
  });

  it('saves a blank hand as not recorded', () => {
    expect(draftFromCustomClub({ ...WEDGE, hand: null }).hand).toBe('');
    expect(payloadFromDraft(newBagClubDraft('sw'))?.hand).toBeNull();
    expect(payloadFromDraft({ ...newBagClubDraft('sw'), hand: 'right' })?.hand).toBe('right');
  });

  it('reports each invalid angle and refuses to build a payload', () => {
    const draft = { ...newBagClubDraft('sw'), loft_deg: '560', bounce_deg: 'x' };

    expect(draftErrors(draft)).toEqual({ loft_deg: 'range', bounce_deg: 'number' });
    expect(payloadFromDraft(draft)).toBeNull();
  });

  it('has no errors for a blank draft', () => {
    expect(draftErrors(newBagClubDraft('pw'))).toEqual({});
  });

  it('tidies and caps text at the server limits', () => {
    const payload = payloadFromDraft({
      ...newBagClubDraft('lw'),
      brand: '  Acme   Golf ',
      model: 'M'.repeat(BAG_TEXT_MAX + 5),
      notes: 'n'.repeat(BAG_NOTES_MAX + 5),
    });

    expect(payload?.brand).toBe('Acme Golf');
    expect(payload?.model).toHaveLength(BAG_TEXT_MAX);
    expect(payload?.notes).toHaveLength(BAG_NOTES_MAX);
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

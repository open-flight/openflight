import { describe, expect, it } from 'vitest';
import type { Shot } from './shot';
import type { BagClub } from '../data/bag';
import { clubSelectionKey, filterShotsByProfile, excludeShotsByProfile, shotClubKey, shotClubLabel } from './shot';

describe('filterShotsByProfile', () => {
  const shotWith = (profileId: string | undefined): Shot => ({ profile_id: profileId, ball_speed_mph: 100 }) as Shot;

  it('keeps only shots stamped with the given profile id', () => {
    const shots = [shotWith('aaa'), shotWith('bbb'), shotWith('aaa')];

    expect(filterShotsByProfile(shots, 'aaa')).toHaveLength(2);
  });

  it('matches exactly, without folding case', () => {
    const shots = [shotWith('AAA'), shotWith('aaa')];

    expect(filterShotsByProfile(shots, 'aaa')).toEqual([shots[1]]);
  });

  it('excludes unstamped shots from every profile', () => {
    const shots = [shotWith(undefined), shotWith('')];

    expect(filterShotsByProfile(shots, 'aaa')).toEqual([]);
    expect(filterShotsByProfile(shots, 'bbb')).toEqual([]);
  });

  it('returns nothing for a blank profile id', () => {
    const shots = [shotWith('aaa'), shotWith(undefined)];

    expect(filterShotsByProfile(shots, '')).toEqual([]);
  });
});

describe('excludeShotsByProfile', () => {
  const shotWith = (profileId: string | undefined): Shot => ({ profile_id: profileId, ball_speed_mph: 100 }) as Shot;

  it('drops only the given profile and keeps unstamped shots', () => {
    const shots = [shotWith('aaa'), shotWith('bbb'), shotWith(undefined)];

    expect(excludeShotsByProfile(shots, 'aaa')).toEqual([shots[1], shots[2]]);
  });

  it('excludes nothing for a blank profile id', () => {
    const shots = [shotWith('aaa'), shotWith('bbb')];

    expect(excludeShotsByProfile(shots, '')).toEqual(shots);
  });
});

describe('bag club keys and labels', () => {
  const wedge = (id: string, model: string): BagClub => ({
    id,
    club: 'sw',
    brand: 'Acme',
    model,
    loft_deg: 56,
    bounce_deg: 10,
    lie_deg: null,
    shaft: '',
    notes: '',
  });
  const shotWith = (overrides: Partial<Shot>): Shot => ({ club: 'sw', ball_speed_mph: 80, ...overrides }) as Shot;

  it('keeps two bag clubs of the same type apart', () => {
    const first = shotWith({ bag_club: wedge('a', 'Spinner') });
    const second = shotWith({ bag_club: wedge('b', 'Digger') });

    expect(shotClubKey(first)).not.toBe(shotClubKey(second));
    expect(shotClubKey(first)).toBe('bag:a');
  });

  it('keys shots without a bag club by type, so bag and plain shots stay apart', () => {
    expect(shotClubKey(shotWith({}))).toBe('sw');
    expect(shotClubKey(shotWith({ bag_club: null }))).toBe('sw');
    expect(shotClubKey(shotWith({ bag_club: wedge('a', 'Spinner') }))).not.toBe('sw');
  });

  it('matches a selection to the shots it records', () => {
    expect(clubSelectionKey('sw', 'a')).toBe(shotClubKey(shotWith({ bag_club: wedge('a', 'Spinner') })));
    expect(clubSelectionKey('sw', '')).toBe(shotClubKey(shotWith({})));
  });

  it('labels a bag shot with loft and model', () => {
    expect(shotClubLabel(shotWith({ bag_club: wedge('a', 'Spinner') }))).toBe('SW 56° Spinner');
  });

  it('labels other shots as before', () => {
    expect(shotClubLabel(shotWith({}))).toBe('sw');
    expect(shotClubLabel(shotWith({ club: 'Driver', training_implement_label: 'The Stack 160g' }))).toBe(
      'The Stack 160g'
    );
  });
});

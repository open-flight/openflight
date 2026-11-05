import { beforeEach, describe, expect, it } from 'vitest';
import { useSystemStore } from '../stores/useSystemStore';
import { ingestSessionClub } from './sessionClubSync';

describe('session club sync', () => {
  beforeEach(() => {
    useSystemStore.setState({ serverClub: null });
  });

  it('adopts club from a session_state snapshot so reload restores it', () => {
    ingestSessionClub('7-iron');

    expect(useSystemStore.getState().serverClub).toEqual({ club: '7-iron', customClubId: '' });
  });

  it('adopts the specific custom club alongside its type', () => {
    ingestSessionClub('sw', 'sw-b');

    expect(useSystemStore.getState().serverClub).toEqual({ club: 'sw', customClubId: 'sw-b' });
  });

  it('reads a missing or null custom club id as none (older servers)', () => {
    ingestSessionClub('sw', null);

    expect(useSystemStore.getState().serverClub).toEqual({ club: 'sw', customClubId: '' });
  });

  it('publishes a fresh selection each time so a repeat still re-applies', () => {
    ingestSessionClub('sw', 'sw-a');
    const first = useSystemStore.getState().serverClub;

    ingestSessionClub('sw', 'sw-a');

    expect(useSystemStore.getState().serverClub).not.toBe(first);
  });

  it('ignores a snapshot that omits club', () => {
    useSystemStore.setState({ serverClub: { club: 'driver', customClubId: '' } });

    ingestSessionClub(undefined, 'sw-a');

    expect(useSystemStore.getState().serverClub).toEqual({ club: 'driver', customClubId: '' });
  });
});

import { beforeEach, describe, expect, it } from 'vitest';
import { useSystemStore } from '../stores/useSystemStore';
import { ingestSessionClub } from './sessionClubSync';

describe('session club sync', () => {
  beforeEach(() => {
    useSystemStore.setState({ serverClub: null });
  });

  it('adopts club from a session_state snapshot so reload restores it', () => {
    ingestSessionClub('7-iron');

    expect(useSystemStore.getState().serverClub).toBe('7-iron');
  });

  it('ignores a snapshot that omits club', () => {
    useSystemStore.setState({ serverClub: 'driver' });

    ingestSessionClub(undefined);

    expect(useSystemStore.getState().serverClub).toBe('driver');
  });
});

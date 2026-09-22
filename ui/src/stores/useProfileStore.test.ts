import { beforeEach, describe, expect, it } from 'vitest';
import { useProfileStore } from './useProfileStore';
import type { Profile } from '../types/profile';

const profile = (id: string, name: string): Profile => ({
  id,
  name,
  created_at: '2026-08-27T10:00:00Z',
  settings: {},
});

describe('useProfileStore', () => {
  beforeEach(() => {
    useProfileStore.setState({ profiles: [], activeProfileId: '', loaded: false });
  });

  it('starts empty and unloaded', () => {
    const state = useProfileStore.getState();

    expect(state.profiles).toEqual([]);
    expect(state.activeProfileId).toBe('');
    expect(state.loaded).toBe(false);
  });

  it('applies a snapshot and marks itself loaded', () => {
    useProfileStore.getState().applySnapshot({
      profiles: [profile('aaa', 'Home'), profile('bbb', 'Range')],
      active_profile_id: 'bbb',
    });

    const state = useProfileStore.getState();
    expect(state.profiles.map((entry) => entry.name)).toEqual(['Home', 'Range']);
    expect(state.activeProfileId).toBe('bbb');
    expect(state.loaded).toBe(true);
  });

  it('replaces state wholesale rather than merging', () => {
    useProfileStore.getState().applySnapshot({
      profiles: [profile('aaa', 'Home'), profile('bbb', 'Range')],
      active_profile_id: 'aaa',
    });

    useProfileStore.getState().applySnapshot({
      profiles: [profile('ccc', 'Course')],
      active_profile_id: 'ccc',
    });

    expect(useProfileStore.getState().profiles.map((entry) => entry.id)).toEqual(['ccc']);
  });

  it('ignores a malformed snapshot instead of blanking the roster', () => {
    useProfileStore.getState().applySnapshot({
      profiles: [profile('aaa', 'Home')],
      active_profile_id: 'aaa',
    });

    useProfileStore.getState().applySnapshot({ profiles: undefined, active_profile_id: '' } as never);

    expect(useProfileStore.getState().profiles.map((entry) => entry.id)).toEqual(['aaa']);
  });
});

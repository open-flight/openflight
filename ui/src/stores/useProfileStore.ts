import { create } from 'zustand';
import type { Profile, ProfilesSnapshot } from '../types/profile';

/**
 * A mirror of the server's roster, not a source of truth.
 *
 * The server owns profiles.json and broadcasts one authoritative `profiles`
 * snapshot after every mutation, so there is nothing to persist here and
 * nothing to reconcile. Deliberately no localStorage: a second copy of the
 * selection is what used to race with the connect-time snapshot.
 */
interface ProfileState {
  profiles: Profile[];
  activeProfileId: string;
  /** False until the first snapshot arrives; the UI shows a skeleton meanwhile. */
  loaded: boolean;
  applySnapshot: (snapshot: ProfilesSnapshot) => void;
}

export const useProfileStore = create<ProfileState>((set) => ({
  profiles: [],
  activeProfileId: '',
  loaded: false,
  applySnapshot: (snapshot) => {
    if (!snapshot || !Array.isArray(snapshot.profiles)) return;
    set({
      profiles: snapshot.profiles,
      activeProfileId: snapshot.active_profile_id ?? '',
      loaded: true,
    });
  },
}));

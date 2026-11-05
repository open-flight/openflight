import { create } from 'zustand';
import { parseClubsSnapshot, type CustomClub } from '../data/bag';

/**
 * A mirror of the server's custom clubs (every bag), not a source of truth.
 * The server owns clubs.json and broadcasts a `clubs` snapshot after every
 * change, so there is nothing to persist or reconcile here.
 */
interface ClubState {
  clubs: CustomClub[];
  /** Each club type's nominal loft: what a blank loft inherits. */
  lofts: Record<string, number>;
  /** Why the server rejected the last save or removal; cleared by the next one. */
  error: string | null;
  applySnapshot: (raw: unknown) => void;
  setError: (error: string | null) => void;
}

export const useClubStore = create<ClubState>((set) => ({
  clubs: [],
  lofts: {},
  error: null,
  applySnapshot: (raw) => set(parseClubsSnapshot(raw)),
  setError: (error) => set({ error }),
}));

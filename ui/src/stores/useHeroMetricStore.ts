import { create } from 'zustand';

const STORAGE_KEY = 'openflight.hero-metric';

/**
 * Which Live metric is selected: yellow title, top-left of the table, and the
 * value that stays pinned in the table's top-left slot. Persisted so someone who
 * cares about club speed keeps that choice across restarts.
 *
 * Stored as a plain metric id; an id that no longer exists is handled by
 * `pinSelectedMetric`, which falls back to the first metric of the current set.
 */
function readStoredHeroMetric(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    return window.localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

interface HeroMetricState {
  heroMetricId: string | null;
  setHeroMetricId: (id: string) => void;
}

export const useHeroMetricStore = create<HeroMetricState>((set) => ({
  heroMetricId: readStoredHeroMetric(),
  setHeroMetricId: (heroMetricId) => {
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(STORAGE_KEY, heroMetricId);
      } catch {
        // Storage can be unavailable (private mode, quota); the choice still
        // applies for this session.
      }
    }

    set({ heroMetricId });
  },
}));

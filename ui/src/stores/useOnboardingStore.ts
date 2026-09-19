import { create } from 'zustand';

export const ONBOARDING_STORAGE_KEY = 'openflight.onboarding.completed:v1';

function readCompleted(): boolean {
  if (typeof window === 'undefined') {
    return false;
  }
  try {
    return window.localStorage.getItem(ONBOARDING_STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

interface OnboardingState {
  completed: boolean;
  complete: () => void;
}

export const useOnboardingStore = create<OnboardingState>((set) => ({
  completed: readCompleted(),
  complete: () => {
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(ONBOARDING_STORAGE_KEY, '1');
      } catch {
        // Storage can be unavailable; this session still counts as complete.
      }
    }
    set({ completed: true });
  },
}));

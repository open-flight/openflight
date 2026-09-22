import { create } from 'zustand';

const SECRET_TAP_WINDOW_MS = 2000;
const SECRET_TAP_TARGET = 5;
const EXPLOSION_DURATION_MS = 2500;

let secretTapCountRef = 0;
let lastTapTime = 0;
let explosionTimer: ReturnType<typeof setTimeout> | null = null;

interface LaunchDaddyState {
  isLaunchDaddyMode: boolean;
  isExploding: boolean;
  toggleLaunchDaddy: () => void;
  triggerExplosion: () => void;
  handleSecretTap: () => void;
}

export const useLaunchDaddyStore = create<LaunchDaddyState>((set, get) => ({
  isLaunchDaddyMode: false,
  isExploding: false,
  toggleLaunchDaddy: () => {
    secretTapCountRef = 0;
    set((state) => ({
      isLaunchDaddyMode: !state.isLaunchDaddyMode,
    }));
  },
  triggerExplosion: () => {
    if (!get().isLaunchDaddyMode) return;

    if (explosionTimer) {
      clearTimeout(explosionTimer);
    }

    set({ isExploding: true });

    explosionTimer = setTimeout(() => {
      set({ isExploding: false });
      explosionTimer = null;
    }, EXPLOSION_DURATION_MS);
  },
  handleSecretTap: () => {
    const now = Date.now();
    const nextCount = now - lastTapTime > SECRET_TAP_WINDOW_MS ? 1 : secretTapCountRef + 1;

    if (nextCount >= SECRET_TAP_TARGET) {
      secretTapCountRef = 0;
      set((state) => ({
        isLaunchDaddyMode: !state.isLaunchDaddyMode,
      }));
    } else {
      secretTapCountRef = nextCount;
    }

    lastTapTime = now;
  },
}));

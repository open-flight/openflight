import { create } from 'zustand';

export const LIVE_VIEW_STORAGE_KEY = 'openflight.live-view:v1';
export const LIVE_VIEW_DURATIONS_MS = [5000, 10000, 15000] as const;

export type LiveViewMode = 'tiles' | 'timed' | 'sticky';
export type LiveViewDurationMs = (typeof LIVE_VIEW_DURATIONS_MS)[number];

const MODES = new Set<LiveViewMode>(['tiles', 'timed', 'sticky']);
const DURATIONS = new Set<number>(LIVE_VIEW_DURATIONS_MS);

export function isLiveViewMode(value: unknown): value is LiveViewMode {
  return typeof value === 'string' && MODES.has(value as LiveViewMode);
}

export function isLiveViewDurationMs(value: unknown): value is LiveViewDurationMs {
  return typeof value === 'number' && DURATIONS.has(value);
}

interface StoredLiveView {
  mode: LiveViewMode;
  durationMs: LiveViewDurationMs;
}

function readStoredLiveView(): StoredLiveView {
  const fallback: StoredLiveView = { mode: 'tiles', durationMs: 10000 };
  if (typeof window === 'undefined') {
    return fallback;
  }

  try {
    const raw = window.localStorage.getItem(LIVE_VIEW_STORAGE_KEY);
    if (!raw) {
      return fallback;
    }
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== 'object') {
      return fallback;
    }
    const { mode, durationMs } = parsed as { mode?: unknown; durationMs?: unknown };
    return {
      mode: isLiveViewMode(mode) ? mode : fallback.mode,
      durationMs: isLiveViewDurationMs(durationMs) ? durationMs : fallback.durationMs,
    };
  } catch {
    return fallback;
  }
}

function persist(state: StoredLiveView): void {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    window.localStorage.setItem(LIVE_VIEW_STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage can be unavailable; the choice still applies for this session.
  }
}

interface LiveViewState extends StoredLiveView {
  setMode: (mode: LiveViewMode) => void;
  setDurationMs: (durationMs: LiveViewDurationMs) => void;
}

export const useLiveViewStore = create<LiveViewState>((set, get) => ({
  ...readStoredLiveView(),
  setMode: (mode) => {
    const next = { mode, durationMs: get().durationMs };
    persist(next);
    set(next);
  },
  setDurationMs: (durationMs) => {
    const next = { mode: get().mode, durationMs };
    persist(next);
    set(next);
  },
}));

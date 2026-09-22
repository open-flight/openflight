import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'openflight.hero-metric';

function installBrowser(initial: Record<string, string> = {}, options: { failWrites?: boolean } = {}) {
  const store = { ...initial };
  const localStorage = {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      if (options.failWrites) {
        throw new Error('quota exceeded');
      }
      store[key] = value;
    },
  };
  vi.stubGlobal('localStorage', localStorage);
  vi.stubGlobal('window', { localStorage });
  return store;
}

async function loadStore() {
  const module = await import('./useHeroMetricStore');
  return module.useHeroMetricStore;
}

describe('useHeroMetricStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('starts with no promoted metric when nothing is stored', async () => {
    installBrowser();
    const useHeroMetricStore = await loadStore();

    expect(useHeroMetricStore.getState().heroMetricId).toBeNull();
  });

  it('restores the promoted metric from storage', async () => {
    installBrowser({ [STORAGE_KEY]: 'spin' });
    const useHeroMetricStore = await loadStore();

    expect(useHeroMetricStore.getState().heroMetricId).toBe('spin');
  });

  it('persists a new choice', async () => {
    const store = installBrowser();
    const useHeroMetricStore = await loadStore();

    useHeroMetricStore.getState().setHeroMetricId('carry');

    expect(useHeroMetricStore.getState().heroMetricId).toBe('carry');
    expect(store[STORAGE_KEY]).toBe('carry');
  });

  it('replaces rather than accumulates the stored id', async () => {
    const store = installBrowser();
    const useHeroMetricStore = await loadStore();

    useHeroMetricStore.getState().setHeroMetricId('spin');
    useHeroMetricStore.getState().setHeroMetricId('club_speed');

    expect(store[STORAGE_KEY]).toBe('club_speed');
  });

  it('keeps the choice for the session when storage rejects the write', async () => {
    installBrowser({}, { failWrites: true });
    const useHeroMetricStore = await loadStore();

    expect(() => useHeroMetricStore.getState().setHeroMetricId('carry')).not.toThrow();
    expect(useHeroMetricStore.getState().heroMetricId).toBe('carry');
  });

  it('falls back to no selection when there is no window (SSR / display route)', async () => {
    vi.stubGlobal('window', undefined);
    const useHeroMetricStore = await loadStore();

    expect(useHeroMetricStore.getState().heroMetricId).toBeNull();
  });
});

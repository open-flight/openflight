import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'openflight.live-view:v1';

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
    removeItem: (key: string) => {
      delete store[key];
    },
  };
  vi.stubGlobal('localStorage', localStorage);
  vi.stubGlobal('window', { localStorage });
  return store;
}

async function loadStore() {
  const module = await import('./useLiveViewStore');
  return module.useLiveViewStore;
}

describe('useLiveViewStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('defaults to tiles and 10s', async () => {
    installBrowser();
    const useLiveViewStore = await loadStore();

    expect(useLiveViewStore.getState().mode).toBe('tiles');
    expect(useLiveViewStore.getState().durationMs).toBe(10000);
  });

  it('restores a valid stored choice', async () => {
    installBrowser({ [STORAGE_KEY]: JSON.stringify({ mode: 'sticky', durationMs: 5000 }) });
    const useLiveViewStore = await loadStore();

    expect(useLiveViewStore.getState().mode).toBe('sticky');
    expect(useLiveViewStore.getState().durationMs).toBe(5000);
  });

  it('falls back when JSON is invalid', async () => {
    installBrowser({ [STORAGE_KEY]: '{not-json' });
    const useLiveViewStore = await loadStore();

    expect(useLiveViewStore.getState().mode).toBe('tiles');
    expect(useLiveViewStore.getState().durationMs).toBe(10000);
  });

  it('falls back when mode or duration is unknown', async () => {
    installBrowser({ [STORAGE_KEY]: JSON.stringify({ mode: 'hero', durationMs: 2500 }) });
    const useLiveViewStore = await loadStore();

    expect(useLiveViewStore.getState().mode).toBe('tiles');
    expect(useLiveViewStore.getState().durationMs).toBe(10000);
  });

  it('restores mixed-validity storage per field', async () => {
    installBrowser({ [STORAGE_KEY]: JSON.stringify({ mode: 'sticky', durationMs: 2500 }) });
    const useLiveViewStore = await loadStore();

    expect(useLiveViewStore.getState().mode).toBe('sticky');
    expect(useLiveViewStore.getState().durationMs).toBe(10000);

    vi.resetModules();
    installBrowser({ [STORAGE_KEY]: JSON.stringify({ mode: 'hero', durationMs: 5000 }) });
    const useLiveViewStoreAgain = await loadStore();

    expect(useLiveViewStoreAgain.getState().mode).toBe('tiles');
    expect(useLiveViewStoreAgain.getState().durationMs).toBe(5000);
  });

  it('persists mode without clearing duration', async () => {
    const store = installBrowser();
    const useLiveViewStore = await loadStore();

    useLiveViewStore.getState().setDurationMs(15000);
    useLiveViewStore.getState().setMode('timed');

    expect(useLiveViewStore.getState().durationMs).toBe(15000);
    expect(JSON.parse(store[STORAGE_KEY])).toEqual({ mode: 'timed', durationMs: 15000 });
  });

  it('keeps the choice when storage rejects the write', async () => {
    installBrowser({}, { failWrites: true });
    const useLiveViewStore = await loadStore();

    expect(() => useLiveViewStore.getState().setMode('sticky')).not.toThrow();
    expect(useLiveViewStore.getState().mode).toBe('sticky');
  });

  it('falls back when there is no window', async () => {
    vi.stubGlobal('window', undefined);
    const useLiveViewStore = await loadStore();

    expect(useLiveViewStore.getState().mode).toBe('tiles');
    expect(useLiveViewStore.getState().durationMs).toBe(10000);
  });
});

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'openflight.onboarding.completed:v1';

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
  const module = await import('./useOnboardingStore');
  return module.useOnboardingStore;
}

describe('useOnboardingStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('starts incomplete when nothing is stored', async () => {
    installBrowser();
    const useOnboardingStore = await loadStore();
    expect(useOnboardingStore.getState().completed).toBe(false);
  });

  it('restores a completed flag', async () => {
    installBrowser({ [STORAGE_KEY]: '1' });
    const useOnboardingStore = await loadStore();
    expect(useOnboardingStore.getState().completed).toBe(true);
  });

  it('writes the flag on complete', async () => {
    const store = installBrowser();
    const useOnboardingStore = await loadStore();
    useOnboardingStore.getState().complete();
    expect(useOnboardingStore.getState().completed).toBe(true);
    expect(store[STORAGE_KEY]).toBe('1');
  });

  it('keeps completed true when storage rejects the write', async () => {
    installBrowser({}, { failWrites: true });
    const useOnboardingStore = await loadStore();
    expect(() => useOnboardingStore.getState().complete()).not.toThrow();
    expect(useOnboardingStore.getState().completed).toBe(true);
  });

  it('starts incomplete when there is no window', async () => {
    vi.stubGlobal('window', undefined);
    const useOnboardingStore = await loadStore();
    expect(useOnboardingStore.getState().completed).toBe(false);
  });
});

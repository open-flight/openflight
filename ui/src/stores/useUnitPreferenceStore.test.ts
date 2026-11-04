import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const STORAGE_KEY = 'openflight.unit-system';

function installBrowser(initial: Record<string, string> = {}) {
  const store = { ...initial };
  const localStorage = {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
  };
  vi.stubGlobal('localStorage', localStorage);
  vi.stubGlobal('window', { localStorage });
  return store;
}

async function loadStore() {
  const module = await import('./useUnitPreferenceStore');
  return module.useUnitPreferenceStore;
}

describe('useUnitPreferenceStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('defaults to imperial when nothing is stored', async () => {
    installBrowser();
    const useUnitPreferenceStore = await loadStore();

    expect(useUnitPreferenceStore.getState().unitSystem).toBe('imperial');
  });

  it.each(['imperial', 'metric', 'mphMeters'] as const)('restores the stored %s preset', async (unitSystem) => {
    installBrowser({ [STORAGE_KEY]: unitSystem });
    const useUnitPreferenceStore = await loadStore();

    expect(useUnitPreferenceStore.getState().unitSystem).toBe(unitSystem);
  });

  it.each(['', 'Metric', 'kmh', 'toString'])('falls back to imperial for unknown stored value %j', async (value) => {
    installBrowser({ [STORAGE_KEY]: value });
    const useUnitPreferenceStore = await loadStore();

    expect(useUnitPreferenceStore.getState().unitSystem).toBe('imperial');
  });

  it('persists the mph / m preset', async () => {
    const store = installBrowser();
    const useUnitPreferenceStore = await loadStore();

    useUnitPreferenceStore.getState().setUnitSystem('mphMeters');

    expect(useUnitPreferenceStore.getState().unitSystem).toBe('mphMeters');
    expect(store[STORAGE_KEY]).toBe('mphMeters');
  });
});

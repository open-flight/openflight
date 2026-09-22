import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { THEME_STORAGE_KEY } from '../theme/theme';

function installBrowser(initial: Record<string, string> = {}, options: { failWrites?: boolean } = {}) {
  const store = { ...initial };
  const dataset: Record<string, string> = {};
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
  vi.stubGlobal('document', { documentElement: { dataset } });
  vi.stubGlobal('window', { localStorage });
  return { dataset, store };
}

describe('useThemeStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('defaults to dark and persists light', async () => {
    const { dataset, store } = installBrowser();
    const { useThemeStore } = await import('./useThemeStore');

    expect(useThemeStore.getState().theme).toBe('dark');

    useThemeStore.getState().setTheme('light');

    expect(useThemeStore.getState().theme).toBe('light');
    expect(store[THEME_STORAGE_KEY]).toBe('light');
    expect(dataset.theme).toBe('light');
  });

  it('hydrates from stored light', async () => {
    installBrowser({ [THEME_STORAGE_KEY]: 'light' });
    const { useThemeStore } = await import('./useThemeStore');
    expect(useThemeStore.getState().theme).toBe('light');
  });

  it('applies the theme for the session when storage rejects the write', async () => {
    const { dataset } = installBrowser({}, { failWrites: true });
    const { useThemeStore } = await import('./useThemeStore');

    expect(() => useThemeStore.getState().setTheme('light')).not.toThrow();
    expect(useThemeStore.getState().theme).toBe('light');
    expect(dataset.theme).toBe('light');
  });
});

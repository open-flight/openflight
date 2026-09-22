import { afterEach, describe, expect, it, vi } from 'vitest';
import { applyTheme, isTheme, readStoredTheme, THEME_STORAGE_KEY } from './theme';

function installBrowser(initial: Record<string, string> = {}) {
  const store = { ...initial };
  const dataset: Record<string, string> = {};
  const localStorage = {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
  };
  vi.stubGlobal('localStorage', localStorage);
  vi.stubGlobal('document', { documentElement: { dataset } });
  vi.stubGlobal('window', { localStorage });
  return { dataset, store };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('theme helpers', () => {
  it('accepts only dark and light', () => {
    expect(isTheme('dark')).toBe(true);
    expect(isTheme('light')).toBe(true);
    expect(isTheme('solar')).toBe(false);
    expect(isTheme(null)).toBe(false);
  });

  it('defaults to dark when storage is missing or invalid', () => {
    installBrowser();
    expect(readStoredTheme()).toBe('dark');
    installBrowser({ [THEME_STORAGE_KEY]: 'nope' });
    expect(readStoredTheme()).toBe('dark');
  });

  it('reads a stored light theme', () => {
    installBrowser({ [THEME_STORAGE_KEY]: 'light' });
    expect(readStoredTheme()).toBe('light');
  });

  it('writes data-theme on the document element', () => {
    const { dataset } = installBrowser();
    applyTheme('light');
    expect(dataset.theme).toBe('light');
  });
});

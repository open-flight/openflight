import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LOCALE_STORAGE_KEY } from '../i18n';

function installBrowser(initial: Record<string, string> = {}, options: { failWrites?: boolean } = {}) {
  const store = { ...initial };
  const documentElement = { lang: 'en' };
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
  vi.stubGlobal('document', { documentElement });
  vi.stubGlobal('window', { localStorage });
  return { documentElement, store };
}

describe('useLocaleStore', () => {
  beforeEach(() => {
    vi.resetModules();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('defaults to English and persists Spanish', async () => {
    const { documentElement, store } = installBrowser();
    const { useLocaleStore } = await import('./useLocaleStore');
    const { t } = await import('../i18n');

    expect(useLocaleStore.getState().locale).toBe('en');
    expect(t('nav.live')).toBe('Live');

    useLocaleStore.getState().setLocale('es');

    expect(useLocaleStore.getState().locale).toBe('es');
    expect(store[LOCALE_STORAGE_KEY]).toBe('es');
    expect(documentElement.lang).toBe('es');
    expect(t('nav.live')).toBe('En vivo');
  });

  it('hydrates from stored Portuguese', async () => {
    const { documentElement } = installBrowser({ [LOCALE_STORAGE_KEY]: 'pt' });
    const { useLocaleStore } = await import('./useLocaleStore');
    const { t } = await import('../i18n');

    expect(useLocaleStore.getState().locale).toBe('pt');
    expect(documentElement.lang).toBe('pt-BR');
    expect(t('nav.live')).toBe('Ao vivo');
  });

  it('applies the locale for the session when storage rejects the write', async () => {
    const { documentElement } = installBrowser({}, { failWrites: true });
    const { useLocaleStore } = await import('./useLocaleStore');

    expect(() => useLocaleStore.getState().setLocale('fr')).not.toThrow();
    expect(useLocaleStore.getState().locale).toBe('fr');
    expect(documentElement.lang).toBe('fr');
  });
});

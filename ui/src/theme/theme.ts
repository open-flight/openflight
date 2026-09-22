export type Theme = 'dark' | 'light';

export const THEME_STORAGE_KEY = 'openflight.theme';

export function isTheme(value: unknown): value is Theme {
  return value === 'dark' || value === 'light';
}

export function readStoredTheme(): Theme {
  if (typeof window === 'undefined') {
    return 'dark';
  }

  try {
    const storedValue = window.localStorage.getItem(THEME_STORAGE_KEY);
    return isTheme(storedValue) ? storedValue : 'dark';
  } catch {
    return 'dark';
  }
}

export function applyTheme(theme: Theme): void {
  if (typeof document === 'undefined') {
    return;
  }

  document.documentElement.dataset.theme = theme;
}

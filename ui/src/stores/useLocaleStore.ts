import { create } from 'zustand';
import {
  applyDocumentLang,
  DEFAULT_LOCALE,
  isLocaleId,
  LOCALE_STORAGE_KEY,
  setActiveLocale,
  type LocaleId,
} from '../i18n';

function readStoredLocale(): LocaleId {
  if (typeof window === 'undefined') {
    return DEFAULT_LOCALE;
  }

  try {
    const storedValue = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    return isLocaleId(storedValue) ? storedValue : DEFAULT_LOCALE;
  } catch {
    return DEFAULT_LOCALE;
  }
}

function persistLocale(locale: LocaleId): void {
  if (typeof window === 'undefined') {
    return;
  }

  try {
    window.localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  } catch {
    // Storage can be unavailable; the locale still applies for this session.
  }
}

function applyLocale(locale: LocaleId): LocaleId {
  const next = setActiveLocale(locale);
  applyDocumentLang(next);
  return next;
}

interface LocaleState {
  locale: LocaleId;
  setLocale: (locale: LocaleId) => void;
}

const initialLocale = applyLocale(readStoredLocale());

export const useLocaleStore = create<LocaleState>((set) => ({
  locale: initialLocale,
  setLocale: (locale) => {
    const next = applyLocale(locale);
    persistLocale(next);
    set({ locale: next });
  },
}));

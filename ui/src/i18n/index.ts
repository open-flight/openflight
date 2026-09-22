import { en, type MessageKey, type Messages } from './en';
import { es } from './es';
import { fr } from './fr';
import { pt } from './pt';

export type LocaleId = 'en' | 'es' | 'fr' | 'pt';

export interface LocaleOption {
  id: LocaleId;
  /** Native name shown in the language dropdown. */
  nativeName: string;
  /** BCP 47 tag for <html lang> and number formatting. */
  htmlLang: string;
}

export const LOCALES: readonly LocaleOption[] = [
  { id: 'en', nativeName: 'English', htmlLang: 'en' },
  { id: 'es', nativeName: 'Español', htmlLang: 'es' },
  { id: 'fr', nativeName: 'Français', htmlLang: 'fr' },
  { id: 'pt', nativeName: 'Português', htmlLang: 'pt-BR' },
];

export const DEFAULT_LOCALE: LocaleId = 'en';
export const LOCALE_STORAGE_KEY = 'openflight.locale:v1';

export const catalogs: Record<LocaleId, Messages> = { en, es, fr, pt };

const localeIds = new Set<string>(LOCALES.map((locale) => locale.id));

export function isLocaleId(value: unknown): value is LocaleId {
  return typeof value === 'string' && localeIds.has(value);
}

let activeLocale: LocaleId = DEFAULT_LOCALE;

export function getActiveLocale(): LocaleId {
  return activeLocale;
}

export function setActiveLocale(locale: string): LocaleId {
  activeLocale = isLocaleId(locale) ? locale : DEFAULT_LOCALE;
  return activeLocale;
}

export function getHtmlLang(locale: LocaleId = activeLocale): string {
  return LOCALES.find((option) => option.id === locale)?.htmlLang ?? 'en';
}

export function applyDocumentLang(locale: LocaleId = activeLocale): void {
  if (typeof document === 'undefined') {
    return;
  }
  document.documentElement.lang = getHtmlLang(locale);
}

const PLACEHOLDER = /\{(\w+)\}/g;

export function t(key: MessageKey, vars?: Record<string, string | number>): string {
  const template = catalogs[activeLocale][key] ?? catalogs.en[key];
  if (!vars) {
    return template;
  }
  return template.replace(PLACEHOLDER, (_match, name: string) => {
    const value = vars[name];
    return value === undefined ? `{${name}}` : String(value);
  });
}

export function clubGroupLabel(name: string): string {
  const key = `clubGroup.${name}` as MessageKey;
  return key in catalogs.en ? t(key) : name;
}

export type { MessageKey, Messages };
export { en };

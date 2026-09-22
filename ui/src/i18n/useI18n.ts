import { useLocaleStore } from '../stores/useLocaleStore';
import { t, type MessageKey } from './index';

/** Subscribe so components re-render when the language changes. */
export function useI18n() {
  const locale = useLocaleStore((state) => state.locale);
  return { locale, t };
}

export type { MessageKey };

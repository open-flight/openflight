import { create } from 'zustand';
import { applyTheme, readStoredTheme, THEME_STORAGE_KEY, type Theme } from '../theme/theme';

interface ThemeState {
  theme: Theme;
  setTheme: (theme: Theme) => void;
}

export const useThemeStore = create<ThemeState>((set) => ({
  theme: readStoredTheme(),
  setTheme: (theme) => {
    if (typeof window !== 'undefined') {
      try {
        window.localStorage.setItem(THEME_STORAGE_KEY, theme);
      } catch {
        // Storage can be unavailable (private mode, quota); the theme still
        // applies for this session.
      }
    }
    applyTheme(theme);
    set({ theme });
  },
}));

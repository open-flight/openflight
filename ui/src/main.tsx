import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import App from './App.tsx';
import { applyTheme, readStoredTheme } from './theme/theme';
import { useLocaleStore } from './stores/useLocaleStore';

applyTheme(readStoredTheme());
useLocaleStore.getState();

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>
);

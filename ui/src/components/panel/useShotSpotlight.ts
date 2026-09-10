import { useEffect, useState } from 'react';
import type { LiveViewMode } from '../../stores/useLiveViewStore';

export function shouldOpenSpotlight(mode: LiveViewMode, isNewShot: boolean): boolean {
  return isNewShot && mode !== 'tiles';
}

export function createSpotlightController(
  mode: LiveViewMode,
  durationMs: number,
  isNewShot: boolean,
  hide: () => void
): { openInitially: boolean; start: () => () => void } {
  const openInitially = shouldOpenSpotlight(mode, isNewShot);
  return {
    openInitially,
    start: () => {
      if (!openInitially || mode !== 'timed') {
        return () => {};
      }
      const timer = setTimeout(hide, durationMs);
      return () => clearTimeout(timer);
    },
  };
}

export function useShotSpotlight(mode: LiveViewMode, durationMs: number, isNewShot: boolean) {
  const [open, setOpen] = useState(() => shouldOpenSpotlight(mode, isNewShot));
  // Freeze mode/duration for this hook instance. LivePanel remounts on a new
  // shot, so menu changes apply then; an overlay already on screen is left alone.
  const [controller] = useState(() => createSpotlightController(mode, durationMs, isNewShot, () => setOpen(false)));

  useEffect(() => {
    return controller.start();
  }, [controller]);

  return { open, dismiss: () => setOpen(false) };
}

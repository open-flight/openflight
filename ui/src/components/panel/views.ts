export type PanelView = 'live' | 'profiles' | 'stats' | 'shots' | 'history' | 'camera' | 'debug';

/**
 * Footer tabs, in order. Design doc 6a uses text-only tabs, so no icons here.
 */
export const PANEL_VIEWS: ReadonlyArray<{ id: PanelView; label: string }> = [
  { id: 'live', label: 'Live' },
  { id: 'stats', label: 'Stats' },
  { id: 'shots', label: 'Shots' },
  { id: 'history', label: 'History' },
  { id: 'camera', label: 'Camera' },
  { id: 'profiles', label: 'Profiles' },
  { id: 'debug', label: 'Debug' },
];

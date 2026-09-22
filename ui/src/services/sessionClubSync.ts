import { useSystemStore } from '../stores/useSystemStore';

/**
 * Adopt the active club from a connect/reload snapshot.
 * Unlike the active profile, the UI does not echo club on connect, so restoring
 * from session_state cannot ping-pong with set_club.
 */
export function ingestSessionClub(club: string | undefined): void {
  if (!club) return;
  useSystemStore.getState().setServerClub(club);
}

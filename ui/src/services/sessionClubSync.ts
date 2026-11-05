import { useSystemStore } from '../stores/useSystemStore';

/**
 * Adopt the active club from a `club_changed` event or a connect/reload snapshot.
 * Unlike the active profile, the UI does not echo club on connect, so restoring
 * from session_state cannot ping-pong with set_club. An older server sends no
 * `custom_club_id`, which reads as "no custom club".
 */
export function ingestSessionClub(club: string | undefined, customClubId?: string | null): void {
  if (!club) return;
  useSystemStore.getState().setServerClub({ club, customClubId: customClubId ?? '' });
}

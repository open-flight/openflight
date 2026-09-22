import type { Shot } from '../types/shot';
import { excludeShotsByProfile } from '../types/shot';

export interface SessionClearedPayload {
  profile_id?: string;
  shots?: Shot[];
}

/** Remaining shots after a clear. Prefer the server list; otherwise drop one profile. */
export function remainingShotsAfterClear(currentShots: Shot[], payload?: SessionClearedPayload | null): Shot[] {
  if (payload?.shots) {
    return payload.shots;
  }
  if (payload?.profile_id) {
    return excludeShotsByProfile(currentShots, payload.profile_id);
  }
  return [];
}

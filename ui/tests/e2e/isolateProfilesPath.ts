import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';

export const PROFILES_PATH_ENV = 'OPENFLIGHT_PROFILES_PATH';
export const CLUBS_PATH_ENV = 'OPENFLIGHT_CLUBS_PATH';

/** Unique roster file for one E2E backend process (shared webServer, keyed by pid). */
export function uniqueE2eProfilesPath(workerId: string | number = process.pid): string {
  return join(mkdtempSync(join(tmpdir(), `openflight-e2e-w${workerId}-`)), 'profiles.json');
}

/** The custom club catalog beside that roster, so E2E never touches ~/.config/openflight. */
export function e2eClubsPathBeside(profilesPath: string): string {
  return join(dirname(profilesPath), 'clubs.json');
}

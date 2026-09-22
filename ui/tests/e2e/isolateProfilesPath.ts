import { mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

export const PROFILES_PATH_ENV = 'OPENFLIGHT_PROFILES_PATH';

/** Unique roster file for one E2E backend process (shared webServer, keyed by pid). */
export function uniqueE2eProfilesPath(workerId: string | number = process.pid): string {
  return join(mkdtempSync(join(tmpdir(), `openflight-e2e-w${workerId}-`)), 'profiles.json');
}


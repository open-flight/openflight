import { describe, expect, it } from 'vitest';
import config from '../playwright.config';
import { PROFILES_PATH_ENV } from './e2e/isolateProfilesPath';

interface WebServerConfig {
  command: string;
  env?: Record<string, string>;
  reuseExistingServer?: boolean;
  url: string;
}

function webServers(): WebServerConfig[] {
  return config.webServer as WebServerConfig[];
}

describe('E2E profile isolation', () => {
  it('starts a fresh isolated backend instead of reusing a live OpenFlight server', () => {
    const backend = webServers().find((server) => server.url === 'http://127.0.0.1:8080');

    expect(backend).toMatchObject({
      reuseExistingServer: false,
    });
    expect(backend?.env?.[PROFILES_PATH_ENV]).toContain('openflight-e2e-w');
  });
});

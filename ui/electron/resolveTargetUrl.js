export const DEFAULT_URL = 'http://localhost:8080';

// Pulled out of main.js so it can be unit-tested without importing the
// `electron` module, which throws outside an actual Electron runtime.
export function resolveTargetUrl(env, argv) {
  return env.OPENFLIGHT_URL || argv[2] || DEFAULT_URL;
}

import { describe, it, expect } from 'vitest';
import { resolveTargetUrl, DEFAULT_URL } from './resolveTargetUrl.js';

describe('resolveTargetUrl', () => {
  it('defaults to the local Flask server when nothing else is set', () => {
    expect(resolveTargetUrl({}, ['electron', 'main.js'])).toBe(DEFAULT_URL);
  });

  it('prefers OPENFLIGHT_URL over the CLI argument', () => {
    expect(
      resolveTargetUrl({ OPENFLIGHT_URL: 'http://pi.local:8080' }, [
        'electron',
        'main.js',
        'http://cli-arg:8080',
      ])
    ).toBe('http://pi.local:8080');
  });

  it('falls back to a CLI argument when the env var is unset', () => {
    expect(resolveTargetUrl({}, ['electron', 'main.js', 'http://cli-arg:8080'])).toBe(
      'http://cli-arg:8080'
    );
  });

  it('ignores an empty OPENFLIGHT_URL rather than passing it through', () => {
    expect(
      resolveTargetUrl({ OPENFLIGHT_URL: '' }, ['electron', 'main.js', 'http://cli-arg:8080'])
    ).toBe('http://cli-arg:8080');
  });
});

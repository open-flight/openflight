import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Profile } from '../../types/profile';
import type { Shot } from '../../types/shot';
import { ProfilesPanel } from './ProfilesPanel';

/** React SSR splits interpolated text with comment markers; drop them. */
function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function profile(id: string, name: string): Profile {
  return { id, name, created_at: '2026-08-27T10:00:00Z', settings: {} };
}

function shot(profileId: string): Shot {
  return { profile_id: profileId, ball_speed_mph: 100 } as Shot;
}

function render(overrides: Partial<Parameters<typeof ProfilesPanel>[0]> = {}) {
  return text(
    renderToString(
      <ProfilesPanel
        profiles={[profile('aaa', 'Home'), profile('bbb', 'Range')]}
        activeProfileId="aaa"
        shots={[shot('aaa'), shot('aaa'), shot('bbb')]}
        loaded={true}
        onSelectProfile={() => {}}
        onRenameProfile={() => {}}
        onRemoveProfile={() => {}}
        {...overrides}
      />
    )
  );
}

describe('ProfilesPanel', () => {
  it('renders every profile with its shot count', () => {
    const html = render();

    expect(html).toContain('Home');
    expect(html).toContain('Range');
    expect(html).toContain('2 shots');
    expect(html).toContain('1 shot');
  });

  it('marks the active profile pressed and the others not', () => {
    const html = render();
    const homeCard = html.match(/<button[^>]*profiles-panel__card[^>]*>[\s\S]*?Home[\s\S]*?<\/button>/)?.[0] ?? '';
    const rangeCard = html.match(/<button[^>]*profiles-panel__card[^>]*>[\s\S]*?Range[\s\S]*?<\/button>/)?.[0] ?? '';

    expect(homeCard).toContain('aria-pressed="true"');
    expect(rangeCard).toContain('aria-pressed="false"');
  });

  it('hides remove on the active profile, shows it on an empty inactive one', () => {
    const html = render({ shots: [shot('aaa'), shot('aaa')] });

    expect(html).not.toContain('aria-label="Remove Home"');
    expect(html).toContain('aria-label="Remove Range"');
  });

  it('hides remove on an inactive profile that still has shots', () => {
    const html = render();

    expect(html).not.toContain('aria-label="Remove Range"');
  });

  it('hides remove entirely when only one profile exists', () => {
    const html = render({ profiles: [profile('aaa', 'Home')] });

    expect(html).not.toContain('aria-label="Remove Home"');
  });

  it('offers rename for every profile, including the active one', () => {
    const html = render();

    expect(html).toContain('aria-label="Rename Home"');
    expect(html).toContain('aria-label="Rename Range"');
  });

  it('groups rename and remove in a right-aligned actions cluster', () => {
    const html = render({ shots: [shot('aaa')] });
    const clusters = [...html.matchAll(/<div class="profiles-panel__actions">[\s\S]*?<\/div>/g)].map(
      (match) => match[0]
    );

    expect(clusters).toHaveLength(2);
    expect(clusters.some((cluster) => cluster.includes('Rename Range') && cluster.includes('Remove Range'))).toBe(true);
    expect(clusters.some((cluster) => cluster.includes('Rename Home') && !cluster.includes('Remove Home'))).toBe(true);
  });

  it('shows a skeleton until the roster arrives, with no profile names in the output', () => {
    const html = render({ loaded: false, profiles: [], activeProfileId: '' });

    expect(html).toContain('aria-busy="true"');
    expect(html).not.toContain('Home');
    expect(html).not.toContain('Range');
  });
});

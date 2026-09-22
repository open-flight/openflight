import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { ReactNode } from 'react';
import type { Shot } from '../../types/shot';
import { StatsPanel } from './StatsPanel';

const css = readFileSync(fileURLToPath(new URL('./panel.css', import.meta.url)), 'utf8');

function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 90,
    club_speed_mph: 67,
    smash_factor: 1.34,
    estimated_carry_yards: 200,
    carry_range: [195, 205],
    club: 'driver',
    profile_id: 'james',
    profile_name: 'James',
    timestamp: '2026-08-19T10:00:00Z',
    peak_magnitude: 100,
    launch_angle_vertical: 13,
    launch_angle_horizontal: 0,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: null,
    club_path_deg: null,
    spin_axis_deg: null,
    spin_rpm: 2600,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: null,
    ...overrides,
  };
}

const render = (shots: Shot[], activeClub = 'driver', headerAction?: ReactNode) =>
  text(
    renderToString(
      <StatsPanel
        shots={shots}
        activeClub={activeClub}
        profileId="james"
        profileName="James"
        headerAction={headerAction}
      />
    )
  );

describe('StatsPanel', () => {
  it('shows an empty state before any shots', () => {
    const html = render([]);

    expect(html).toContain('No shots yet');
    expect(html).not.toContain('stats-panel__grid');
    expect(html).not.toContain('All (0)');
    expect(html).not.toContain('Filter by club');
  });

  it('renders six tiles for a ball-strike session', () => {
    const html = render([makeShot(), makeShot({ ball_speed_mph: 96, timestamp: 'b' })]);

    expect(html).toContain('stats-panel__grid--of-6');
    for (const label of ['Shots', 'Avg ball', 'Max ball', 'Avg carry', 'Avg club', 'Avg smash']) {
      expect(html).toContain(`>${label}<`);
    }
    expect(html).toContain('metric-card__value">93.0<'); // avg of 90 and 96
    expect(html).toContain('metric-card__value">96.0<'); // max
  });

  it('keeps the tile count stable when club speed is missing', () => {
    // Six tiles either way, so the 3x2 grid never reflows.
    const html = render([makeShot({ club_speed_mph: null, smash_factor: null })]);

    expect(html).toContain('stats-panel__grid--of-6');
    expect(html).toContain('>Avg club<');
    expect(html).toContain('metric-card__value">—<');
  });

  it('renders four tiles for a swing-speed session', () => {
    const swing = makeShot({ mode: 'swing-speed', club: 'Swing Speed' });
    const html = render([swing], 'Swing Speed');

    expect(html).toContain('stats-panel__grid--of-4');
    for (const label of ['Swings', 'Last', 'Best', 'Average']) {
      expect(html).toContain(`>${label}<`);
    }
  });

  it('offers an All chip plus one per club, with counts', () => {
    const html = render([makeShot(), makeShot({ club: '7-iron', timestamp: 'b' })]);

    expect(html).toContain('All (2)');
    expect(html).toContain('DRIVER (1)');
    expect(html).toContain('7-IRON (1)');
  });

  it('puts club filters above the metric tiles, not in the header', () => {
    // Extra clubs used to live in panel-header__actions and clip the title.
    const html = render([
      makeShot(),
      makeShot({ club: '3-wood', timestamp: 'b' }),
      makeShot({ club: '5-iron', timestamp: 'c' }),
      makeShot({ club: 'pw', timestamp: 'd' }),
    ]);
    const header = html.match(/<header class="panel-header">[\s\S]*?<\/header>/)?.[0] ?? '';

    expect(header).not.toContain('Filter by club');
    expect(html).toContain('stats-panel__chips');
    expect(html.indexOf('stats-panel__chips')).toBeLessThan(html.indexOf('stats-panel__grid'));
    expect(html).toContain('PW (1)');
  });

  it('places Clear session in the header', () => {
    const html = render(
      [],
      'driver',
      <button type="button" className="panel-action">
        Clear session
      </button>
    );
    const header = html.match(/<header class="panel-header">[\s\S]*?<\/header>/)?.[0] ?? '';

    expect(header).toContain('Clear session');
  });

  it('keeps All fixed while the club chips scroll', () => {
    const html = render([
      makeShot(),
      makeShot({ club: '3-wood', timestamp: 'b' }),
      makeShot({ club: 'pw', timestamp: 'c' }),
    ]);
    const scroller = html.match(/stats-panel__chip-scroll[\s\S]*?<\/div>/)?.[0] ?? '';

    expect(html.indexOf('All (3)')).toBeLessThan(html.indexOf('stats-panel__chip-scroll'));
    expect(scroller).not.toContain('All (');
    expect(scroller).toContain('DRIVER (1)');
    expect(scroller).toContain('PW (1)');
  });

  it('preselects the active club when it has shots', () => {
    const html = render([makeShot(), makeShot({ club: '7-iron', timestamp: 'b' })], '7-iron');
    const activeChip = html.match(/<button[^>]*panel-chip--active[^>]*>([^<]*)</)?.[1];

    expect(activeChip).toBe('7-IRON (1)');
    // Stats reflect the filter, not the whole session.
    expect(html).toContain('metric-card__value">1<');
  });

  it('falls back to All when the active club has no shots yet', () => {
    const html = render([makeShot()], 'sw');
    const activeChip = html.match(/<button[^>]*panel-chip--active[^>]*>([^<]*)</)?.[1];

    expect(activeChip).toBe('All (1)');
  });

  it('computes averages and club chips from the current profile only', () => {
    const html = render([
      makeShot({ ball_speed_mph: 90, timestamp: 'a' }),
      makeShot({ profile_id: 'alex', profile_name: 'Alex', ball_speed_mph: 150, club: '7-iron', timestamp: 'b' }),
    ]);

    expect(html).toContain('All (1)');
    expect(html).toContain('DRIVER (1)');
    expect(html).not.toContain('7-IRON');
    expect(html).toContain('metric-card__value">1<');
    expect(html).toContain('metric-card__value">90.0<');
    expect(html).not.toContain('metric-card__value">150.0<');
    expect(html).not.toContain('metric-card__value">120.0<');
  });

  it('shows the empty state when only other profiles have shots', () => {
    const html = render([makeShot({ profile_id: 'alex', profile_name: 'Alex' })]);

    expect(html).toContain('No shots yet');
    expect(html).not.toContain('stats-panel__grid');
    expect(html).not.toContain('All (0)');
    expect(html).not.toContain('Filter by club');
  });

  it('paints chips and tiles on the same surface', () => {
    // Light theme made this look like a cream chip gutter over a white spreadsheet.
    expect(css).toMatch(/\.stats-panel \{[^}]*background: var\(--color-surface\)/);
    expect(css).not.toMatch(/\.stats-panel__grid \.metric-card--label-above \{[^}]*align-content: center/);
  });
});

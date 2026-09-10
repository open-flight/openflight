import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import type { Shot } from '../../types/shot';
import { useLiveViewStore } from '../../stores/useLiveViewStore';
import { LivePanel } from './LivePanel';

/** React SSR splits interpolated text with comment markers; drop them. */
function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function makeShot(overrides: Partial<Shot> = {}): Shot {
  return {
    ball_speed_mph: 92,
    club_speed_mph: 68,
    smash_factor: 1.35,
    estimated_carry_yards: 210,
    carry_range: [205, 215],
    club: 'driver',
    timestamp: '2026-08-19T10:00:00Z',
    peak_magnitude: 100,
    launch_angle_vertical: 13.4,
    launch_angle_horizontal: -1.2,
    launch_angle_confidence: 0.8,
    angle_source: 'radar',
    club_angle_deg: 2.1,
    club_path_deg: -0.6,
    spin_axis_deg: 3.4,
    spin_rpm: 2650,
    spin_confidence: 0.9,
    spin_quality: 'high',
    spin_source: 'measured',
    spin_method: null,
    carry_spin_adjusted: 214,
    profile_id: 'james',
    profile_name: 'James',
    ...overrides,
  };
}

function render(
  shot: Shot | null,
  shots: Shot[] = shot ? [shot] : [],
  selectedMetricId: string | null = null,
  isNewShot = false,
  mode: 'tiles' | 'timed' | 'sticky' = 'tiles'
) {
  useLiveViewStore.setState({ mode, durationMs: 10000 });
  return text(
    renderToString(
      <LivePanel
        shot={shot}
        shots={shots}
        profileId="james"
        profileName="James"
        clubLabel="DR"
        selectedMetricId={selectedMetricId}
        onSelectMetric={() => {}}
        isNewShot={isNewShot}
      />
    )
  );
}

function tileLabels(html: string): string[] {
  return [...html.matchAll(/metric-card__label[^>]*>([^<]+)/g)].map((match) => match[1]);
}

describe('LivePanel', () => {
  it('sizes live metric numbers in rem that follow the screen', () => {
    const css = readFileSync(fileURLToPath(new URL('./panel.css', import.meta.url)), 'utf8');
    const rootCss = readFileSync(fileURLToPath(new URL('../../index.css', import.meta.url)), 'utf8');

    expect(rootCss).toMatch(/html \{[^}]*font-size: clamp\([^)]*vw[^)]*150%/);
    expect(css).toMatch(
      /\.live-panel__grid \.metric-card__value-row \{[^}]*font-size: clamp\([\d.]+rem, min\([^)]*vw[^)]*vh[^)]*cqi[^)]*cqb\), [\d.]+rem\)/
    );
    expect(css).toMatch(/\.live-panel__grid \.metric-card__value \{[^}]*font-size: 1em/);
    expect(css).toMatch(/\.live-panel__grid \.metric-card__value \{[^}]*flex-shrink: 0/);
    expect(css).toMatch(/\.live-panel__grid \.metric-card__unit \{[^}]*font-size: 0\.42em/);
    expect(css).toMatch(/\.live-panel__grid \.metric-card__value \{[^}]*letter-spacing: 0/);
    expect(css).toMatch(/\.live-panel__grid \.metric-card__value-row \{[^}]*gap: 0\.12em/);
    expect(css).toMatch(/\.live-panel__grid \.metric-card__value-row \{[^}]*overflow: hidden/);
    expect(css).not.toMatch(/\.live-panel__grid \.metric-card__value \{[^}]*font-size: clamp\(\d+px/);
  });

  it('wraps live confidence labels the same way as long subtitles', () => {
    const css = readFileSync(fileURLToPath(new URL('./panel.css', import.meta.url)), 'utf8');

    expect(css).toMatch(
      /\.live-panel__grid \.metric-card__subtext,\s*\.live-panel__grid \.metric-card__confidence-label \{[^}]*white-space: normal/
    );
    expect(css).toMatch(
      /\.live-panel__grid \.metric-card__subtext,\s*\.live-panel__grid \.metric-card__confidence-label \{[^}]*overflow-wrap: anywhere/
    );
    expect(css).toMatch(/\.live-panel__grid \.metric-card__confidence \{[^}]*flex-wrap: wrap/);
    expect(css).toMatch(/\.live-panel__grid \.metric-card__confidence-label \{[^}]*min-width: 0/);
  });

  it('shares one live number size across tiles instead of shrinking per card', () => {
    const liveSrc = readFileSync(fileURLToPath(new URL('./LivePanel.tsx', import.meta.url)), 'utf8');
    const cardSrc = readFileSync(fileURLToPath(new URL('../ui/MetricCard.tsx', import.meta.url)), 'utf8');

    expect(liveSrc).toContain('useSharedFitFontSize');
    expect(cardSrc).not.toContain('useFitFontSize');
    expect(cardSrc).not.toContain('useSharedFitFontSize');
  });

  it('shows the ready state before the first shot', () => {
    const html = render(null);

    expect(html).toContain('Ready');
    expect(html).toContain('live-panel__empty-title');
    expect(html).not.toContain('live-panel__spotlight');
    expect(html).not.toContain('metric-card--interactive');
  });

  it('renders all ten metrics in a table with no hero slot', () => {
    const html = render(makeShot());

    expect(html).not.toContain('live-panel__hero');
    expect(html).toContain('live-panel__grid--of-10');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(10);
    expect(html).toContain('>Club AoA<');
    expect(tileLabels(html)[0]).toBe('Ball speed');
  });

  it('pins the selected metric to the top left and marks its title selected', () => {
    const html = render(makeShot(), undefined, 'spin');

    expect(tileLabels(html)[0]).toBe('Spin');
    expect(html).toContain('metric-card--selected');
    expect(html).toMatch(/metric-card--selected[\s\S]*?>Spin</);
    expect(html).toContain('>Ball speed<');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(10);
  });

  it('falls back to ball speed when the selected metric is not in this set', () => {
    const html = render(makeShot(), undefined, 'swing_best');

    expect(tileLabels(html)[0]).toBe('Ball speed');
    expect(html).toMatch(/metric-card--selected[\s\S]*?>Ball speed</);
  });

  it('keeps every metric visible after a new shot in tiles mode', () => {
    const html = render(makeShot(), undefined, 'spin', true, 'tiles');

    expect(html).not.toContain('live-panel__spotlight');
    expect(html).toContain('live-panel__grid--of-10');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(10);
    expect(html).toContain('shot-flash');
  });

  it('overlays the selected metric after a new shot in timed mode', () => {
    const html = render(makeShot(), undefined, 'spin', true, 'timed');

    expect(html).toContain('live-panel__spotlight');
    expect(html).toContain('shot-flash');
    expect(html).toContain('aria-label="Hide shot overlay"');
    expect(html).toContain('>2,650<');
    expect(html).toContain('live-panel__grid--of-10');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(10);
  });

  it('plays the shot flash above the single-metric overlay', () => {
    const css = readFileSync(fileURLToPath(new URL('./panel.css', import.meta.url)), 'utf8');
    const spotlight = css.match(/\.live-panel__spotlight \{[^}]*z-index:\s*(\d+)/);
    const flash = css.match(/\.shot-flash \{[^}]*z-index:\s*(\d+)/);

    expect(spotlight?.[1]).toBeTruthy();
    expect(flash?.[1]).toBeTruthy();
    expect(Number(flash?.[1])).toBeGreaterThan(Number(spotlight?.[1]));
  });

  it('overlays the selected metric after a new shot in sticky mode', () => {
    const html = render(makeShot(), undefined, 'spin', true, 'sticky');

    expect(html).toContain('live-panel__spotlight');
    expect(html).toContain('shot-flash');
    expect(html).toContain('live-panel__grid--of-10');
  });

  it('does not show the spotlight for a restored session shot', () => {
    const html = render(makeShot(), undefined, null, false, 'timed');

    expect(html).not.toContain('live-panel__spotlight');
    expect(html).not.toContain('shot-flash');
  });

  it('does not show the spotlight on ready in timed mode', () => {
    const html = render(null, [], null, false, 'timed');

    expect(html).toContain('Ready');
    expect(html).not.toContain('live-panel__spotlight');
  });

  it('marks estimated tiles with an icon instead of provenance copy', () => {
    const html = render(makeShot({ angle_source: 'estimated', spin_source: 'calculated' }));

    expect(html).toContain('metric-card__estimated');
    expect(html).not.toContain('>estimated<');
    expect(html).not.toContain('>radar<');
  });

  it('marks experimental tiles with the flask icon from live metrics', () => {
    const html = render(
      makeShot({
        club_angle_deg: null,
        club_path_deg: null,
        experimental_fused_attack_angle_deg: -4.2,
        experimental_fused_club_path_deg: 3.1,
        experimental_fused_status: 'approach_mixed',
        launch_angle_horizontal_source: 'camera_assisted_experimental',
      })
    );

    expect(html).toContain('metric-card__experimental');
    expect(html).toContain('metric-card__experimental-label">Experimental<');
  });

  it('shows the experimental mark on the timed spotlight, not only on tiles', () => {
    const html = render(
      makeShot({
        club_path_deg: null,
        experimental_fused_club_path_deg: 3.1,
        experimental_fused_club_path_confidence: 'high',
        experimental_fused_status: 'approach_mixed',
      }),
      undefined,
      'club_path',
      true,
      'timed'
    );
    const spotlight = html.match(/class="live-panel__spotlight"[\s\S]*?<\/button>/)?.[0] ?? '';

    expect(spotlight).toContain('live-panel__spotlight');
    expect(spotlight).toContain('metric-card__experimental');
    expect(spotlight).toContain('metric-card__experimental-label">Experimental<');
  });

  it('makes every tile a pressable button so it can be selected', () => {
    const html = render(makeShot());

    expect(html.match(/<button[^>]*metric-card--interactive/g)).toHaveLength(10);
    expect(html).toContain('aria-pressed="true"');
  });

  it('shows the profile and club in the header', () => {
    const html = render(makeShot(), [makeShot(), makeShot(), makeShot()]);

    expect(html).toContain('panel-header__subtitle">James<');
    expect(html).toContain('panel-header__club">DR<');
    expect(html).not.toContain('mph / yds');
    expect(html).not.toContain('Shot 03');
  });

  it('places Change club in the header actions', () => {
    const html = text(
      renderToString(
        <LivePanel
          shot={null}
          shots={[]}
          profileId="james"
          profileName="James"
          clubLabel="DR"
          headerAction={
            <button type="button" className="panel-action">
              Change club
            </button>
          }
        />
      )
    );
    const header = html.match(/<header class="panel-header">[\s\S]*?<\/header>/)?.[0] ?? '';

    expect(header).toContain('Change club');
    expect(header).toContain('panel-action');
  });

  it("shows the current profile's last shot, not the previous profile's", () => {
    const james = makeShot({ ball_speed_mph: 90, timestamp: 'a' });
    const alex = makeShot({ profile_id: 'alex', profile_name: 'Alex', ball_speed_mph: 150, timestamp: 'b' });
    const html = render(alex, [james, alex]);

    expect(html).toContain('>90.0<');
    expect(html).not.toContain('>150.0<');
    expect(html).not.toContain('Ready');
  });

  it('returns to ready when the current profile has no shots', () => {
    const alex = makeShot({ profile_id: 'alex', profile_name: 'Alex', ball_speed_mph: 150, timestamp: 'b' });
    const html = render(alex, [alex]);

    expect(html).toContain('Ready');
    expect(html).not.toContain('metric-card--interactive');
  });

  it('renders the five-tile grid for a swing-speed shot', () => {
    const swing = makeShot({
      mode: 'swing-speed',
      training_implement: 'stack-100g',
      training_implement_label: 'Stack 100g',
      profile_id: 'james',
      profile_name: 'James',
    });
    const html = text(
      renderToString(
        <LivePanel
          shot={swing}
          shots={[swing]}
          profileId="james"
          profileName="James"
          clubLabel="Stack 100g"
          activeTrainingImplement="stack-100g"
          onSelectMetric={() => {}}
        />
      )
    );

    expect(html).not.toContain('live-panel__hero');
    expect(html).toContain('live-panel__grid--of-5');
    expect(html.match(/metric-card--interactive/g)).toHaveLength(5);
    expect(tileLabels(html)[0]).toBe('Last swing');
  });

  it('does not warn when ball detection is off', () => {
    expect(render(makeShot())).not.toContain('live-panel__ball-warning');
    expect(render(null)).not.toContain('live-panel__ball-warning');
  });

  it('shows a full-screen warning when ball detection is on and no ball is found', () => {
    const html = text(
      renderToString(
        <LivePanel
          shot={makeShot()}
          shots={[makeShot()]}
          profileId="james"
          profileName="James"
          clubLabel="DR"
          ballDetectionEnabled
          ballDetected={false}
        />
      )
    );

    expect(html).toContain('live-panel__ball-warning');
    expect(html).toContain('No ball detected');
    expect(html).toContain('role="alert"');
  });

  it('still warns on the ready screen so a swing is not taken without a ball', () => {
    const html = text(
      renderToString(
        <LivePanel
          shot={null}
          shots={[]}
          profileId="james"
          profileName="James"
          clubLabel="DR"
          ballDetectionEnabled
          ballDetected={false}
        />
      )
    );

    expect(html).toContain('No ball detected');
  });

  it('hides the warning once a ball is detected', () => {
    const html = text(
      renderToString(
        <LivePanel
          shot={makeShot()}
          shots={[makeShot()]}
          profileId="james"
          profileName="James"
          clubLabel="DR"
          ballDetectionEnabled
          ballDetected
        />
      )
    );

    expect(html).not.toContain('live-panel__ball-warning');
  });
});

import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { OnboardingFlow } from './OnboardingFlow';
import { useLiveViewStore } from '../../stores/useLiveViewStore';

function render(initialStep?: 'welcome' | 'language' | 'theme' | 'live' | 'done') {
  return renderToString(<OnboardingFlow onFinished={() => {}} initialStep={initialStep} />).replace(/<!-- -->/g, '');
}

describe('OnboardingFlow', () => {
  it('shows welcome with Get started and no footer chrome', () => {
    const html = render();
    expect(html).toContain('Get started');
    expect(html).toContain('OpenFlight');
    expect(html).toContain('onboarding__mark');
    expect(html).toContain('src="/openflightlogo.svg"');
    expect(html).not.toContain('Continue');
    expect(html).not.toContain('panel-footer');
  });

  it('puts language and units on one screen', () => {
    const html = render('language');
    expect(html).toContain('English');
    expect(html).toContain('Español');
    expect(html).toContain('Français');
    expect(html).toContain('Português');
    expect(html).toContain('MPH / YDS');
    expect(html).toContain('KMH / M');
    expect(html).toContain('Continue');
    expect(html).toContain('Back');
  });

  it('offers dark and light theme tiles', () => {
    const html = render('theme');
    expect(html).toContain('Dark');
    expect(html).toContain('Light');
    expect(html).toContain('onboarding__tile--dark');
    expect(html).toContain('onboarding__tile--light');
    expect(html).toContain('onboarding__theme-swatch');
  });

  it('hides duration chips unless timed preview is selected', () => {
    useLiveViewStore.setState({ mode: 'tiles', durationMs: 10000 });
    expect(render('live')).not.toContain('>5s<');

    useLiveViewStore.setState({ mode: 'timed', durationMs: 10000 });
    const html = render('live');
    expect(html).toContain('>5s<');
    expect(html).toContain('>10s<');
    expect(html).toContain('>15s<');
    expect(html.indexOf('Timed preview')).toBeLessThan(html.indexOf('>5s<'));
    expect(html.indexOf('>5s<')).toBeLessThan(html.indexOf('Hold preview'));
    expect(html).toContain('Tiles');
    expect(html).toContain('Timed preview');
    expect(html).toContain('Hold preview');
    expect(html).toContain('onboarding__live-demo--tiles');
    expect(html).toContain('onboarding__live-demo--timed');
    expect(html).toContain('onboarding__live-demo--sticky');
  });

  it('uses Start on the done screen', () => {
    const html = render('done');
    expect(html).toContain('You&#x27;re ready');
    expect(html).toContain('Start');
    expect(html).not.toContain('Continue');
  });
});

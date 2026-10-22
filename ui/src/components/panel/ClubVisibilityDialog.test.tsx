import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ClubVisibilityDialog } from './ClubVisibilityDialog';

/** React SSR splits interpolated text with comment markers; drop them. */
function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function render(overrides: Partial<Parameters<typeof ClubVisibilityDialog>[0]> = {}) {
  return text(
    renderToString(
      <ClubVisibilityDialog
        profileName="Home"
        enabledClubIds={undefined}
        onSave={() => {}}
        onCancel={() => {}}
        {...overrides}
      />
    )
  );
}

describe('ClubVisibilityDialog', () => {
  it('titles itself with the profile name', () => {
    const html = render({ profileName: 'Cormac' });

    expect(html).toContain('aria-label="Cormac&#x27;s clubs"');
    expect(html).toContain('>Cormac&#x27;s clubs<');
  });

  it('lists every club grouped by family', () => {
    const html = render();

    expect(html).toContain('>Irons<');
    expect(html).toContain('>Hybrids<');
    expect(html).toContain('>Woods<');
    expect(html).toContain('>DR<');
    expect(html).toContain('>7i<');
    expect(html).toContain('>PW<');
  });

  it('pre-checks every club when enabledClubIds is undefined', () => {
    const html = render();
    const driverBtn = html.match(/<button[^>]*>DR<\/button>/)?.[0] ?? '';

    expect(driverBtn).toContain('aria-pressed="true"');
  });

  it('pre-checks every club when enabledClubIds is empty', () => {
    const html = render({ enabledClubIds: [] });
    const driverBtn = html.match(/<button[^>]*>DR<\/button>/)?.[0] ?? '';

    expect(driverBtn).toContain('aria-pressed="true"');
  });

  it('only pre-checks the clubs named in enabledClubIds', () => {
    const html = render({ enabledClubIds: ['driver', '7-iron'] });
    const driverBtn = html.match(/<button[^>]*>DR<\/button>/)?.[0] ?? '';
    const sevenIronBtn = html.match(/<button[^>]*>7i<\/button>/)?.[0] ?? '';
    const pwBtn = html.match(/<button[^>]*>PW<\/button>/)?.[0] ?? '';

    expect(driverBtn).toContain('aria-pressed="true"');
    expect(sevenIronBtn).toContain('aria-pressed="true"');
    expect(pwBtn).toContain('aria-pressed="false"');
  });

  it('offers a save and a cancel action', () => {
    const html = render();

    expect(html).toContain('>Save<');
    expect(html).toContain('>Cancel<');
  });
});

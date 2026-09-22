import { renderToString } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import { ProfileNameDialog } from './ProfileNameDialog';

/** React SSR splits interpolated text with comment markers; drop them. */
function text(html: string): string {
  return html.replace(/<!-- -->/g, '');
}

function render(overrides: Partial<Parameters<typeof ProfileNameDialog>[0]> = {}) {
  return text(
    renderToString(
      <ProfileNameDialog
        mode="add"
        name=""
        onChange={() => {}}
        onConfirm={() => {}}
        onCancel={() => {}}
        {...overrides}
      />
    )
  );
}

describe('ProfileNameDialog', () => {
  it('titles itself for the add mode', () => {
    const html = render();

    expect(html).toContain('aria-label="Add profile"');
    expect(html).toContain('>Add profile<');
  });

  it('titles itself for the rename mode', () => {
    const html = render({ mode: 'rename', name: 'Home' });

    expect(html).toContain('aria-label="Rename profile"');
    expect(html).toContain('>Rename profile<');
  });

  it('caps the name at 40 characters', () => {
    const html = render();

    expect(html).toContain('maxLength="40"');
  });

  it('disables confirm for a blank name', () => {
    const html = render({ name: '   ' });
    const confirmButton = html.match(/<button[^>]*panel-action--primary[^>]*>[\s\S]*?<\/button>/)?.[0] ?? '';

    expect(confirmButton).toContain('disabled=""');
  });

  it('does not disable confirm when the name has content', () => {
    const html = render({ name: 'Range' });
    const confirmButton = html.match(/<button[^>]*panel-action--primary[^>]*>[\s\S]*?<\/button>/)?.[0] ?? '';

    expect(confirmButton).not.toContain('disabled=""');
  });

  it('ships an on-screen keyboard and keeps the native OSK down', () => {
    const html = render();

    expect(html).toContain('aria-label="Keyboard"');
    expect(html).toContain('>Q<');
    expect(html).toContain('aria-label="Backspace"');
    expect(html).toContain('aria-label="Space"');
    expect(html).toContain('inputMode="none"');
  });
});

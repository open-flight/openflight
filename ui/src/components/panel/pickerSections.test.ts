import { describe, expect, it } from 'vitest';
import {
  clubSections,
  filterSectionsByEnabledClubs,
  initialPickerSection,
  pickerGridRows,
  trainingImplementSections,
} from './pickerSections';

describe('initialPickerSection', () => {
  const clubs = clubSections();

  it('opens Woods when the driver is selected', () => {
    expect(initialPickerSection(clubs, 'driver')).toBe('Woods');
  });

  it('opens Irons when a wedge is selected', () => {
    expect(initialPickerSection(clubs, 'pw')).toBe('Irons');
  });

  it('falls back to the first section when the id is unknown', () => {
    expect(initialPickerSection(clubs, 'not-a-club')).toBe('Irons');
  });

  it('opens the matching training group', () => {
    const groups = trainingImplementSections();
    expect(initialPickerSection(groups, 'stack-160g')).toBe('TheStack');
  });
});

describe('pickerGridRows', () => {
  it('uses three rows for the iron set so woods tiles match that cube size', () => {
    expect(pickerGridRows(clubSections())).toBe(3);
  });

  it('uses the densest training group so every tab fits', () => {
    expect(pickerGridRows(trainingImplementSections())).toBe(4);
  });
});

describe('filterSectionsByEnabledClubs', () => {
  const clubs = clubSections();

  it('returns all sections unchanged when enabledClubIds is undefined', () => {
    expect(filterSectionsByEnabledClubs(clubs, undefined)).toBe(clubs);
  });

  it('returns all sections unchanged when enabledClubIds is empty', () => {
    expect(filterSectionsByEnabledClubs(clubs, [])).toBe(clubs);
  });

  it('filters each section down to the enabled club ids', () => {
    const filtered = filterSectionsByEnabledClubs(clubs, ['driver', '7-iron', 'pw']);

    expect(filtered.map((section) => section.name)).toEqual(['Irons', 'Woods']);
    expect(filtered.find((section) => section.name === 'Irons')?.options.map((o) => o.id)).toEqual(['7-iron', 'pw']);
    expect(filtered.find((section) => section.name === 'Woods')?.options.map((o) => o.id)).toEqual(['driver']);
  });

  it('drops a section entirely when none of its clubs are enabled', () => {
    const filtered = filterSectionsByEnabledClubs(clubs, ['driver']);

    expect(filtered.map((section) => section.name)).toEqual(['Woods']);
  });

  it('ignores unknown club ids without throwing', () => {
    const filtered = filterSectionsByEnabledClubs(clubs, ['driver', 'not-a-club']);

    expect(filtered.map((section) => section.name)).toEqual(['Woods']);
  });
});

import { describe, expect, it } from 'vitest';
import {
  convertDistanceFromYards,
  convertSpeedFromMph,
  formatCarryRange,
  formatDistance,
  formatSpeed,
  getDistanceUnit,
  getSpeedUnit,
  getUnitsLabel,
  isUnitSystem,
  UNIT_SYSTEM_IDS,
} from './units';

describe('units helpers', () => {
  it('preserves imperial speed values', () => {
    expect(convertSpeedFromMph(150, 'imperial')).toBe(150);
    expect(formatSpeed(150, 'imperial', 1)).toBe('150.0');
    expect(getSpeedUnit('imperial')).toBe('mph');
  });

  it('converts mph to km/h with stable rounding', () => {
    expect(convertSpeedFromMph(100, 'metric')).toBeCloseTo(160.934, 3);
    expect(formatSpeed(100, 'metric', 1)).toBe('160.9');
    expect(getSpeedUnit('metric')).toBe('km/h');
  });

  it('preserves imperial distance values', () => {
    expect(convertDistanceFromYards(250, 'imperial')).toBe(250);
    expect(formatDistance(250, 'imperial', 0)).toBe('250');
    expect(getDistanceUnit('imperial')).toBe('yds');
  });

  it('converts yards to meters with stable rounding', () => {
    expect(convertDistanceFromYards(100, 'metric')).toBeCloseTo(91.44, 2);
    expect(formatDistance(100, 'metric', 0)).toBe('91');
    expect(getDistanceUnit('metric')).toBe('m');
  });

  it('joins speed and distance units for chrome labels', () => {
    expect(getUnitsLabel('imperial')).toBe('mph / yds');
    expect(getUnitsLabel('metric')).toBe('km/h / m');
    expect(getUnitsLabel('mphMeters')).toBe('mph / m');
  });

  it('keeps mph but converts carry to meters for the mph / m preset', () => {
    expect(convertSpeedFromMph(150, 'mphMeters')).toBe(150);
    expect(formatSpeed(150, 'mphMeters', 1)).toBe('150.0');
    expect(getSpeedUnit('mphMeters')).toBe('mph');
    expect(convertDistanceFromYards(100, 'mphMeters')).toBeCloseTo(91.44, 2);
    expect(formatDistance(100, 'mphMeters', 0)).toBe('91');
    expect(getDistanceUnit('mphMeters')).toBe('m');
    expect(formatCarryRange([200, 220], 'mphMeters')).toBe('183-201 m');
  });

  it('lists the unit presets in menu order', () => {
    expect(UNIT_SYSTEM_IDS).toEqual(['imperial', 'metric', 'mphMeters']);
  });

  it('recognises only known unit presets', () => {
    for (const id of UNIT_SYSTEM_IDS) {
      expect(isUnitSystem(id)).toBe(true);
    }
    for (const value of [null, undefined, '', 'Metric', 'mph-meters', 'toString', '__proto__', 1]) {
      expect(isUnitSystem(value)).toBe(false);
    }
  });

  it('formats carry ranges in the selected unit system', () => {
    expect(formatCarryRange([200, 220], 'imperial')).toBe('200-220 yds');
    expect(formatCarryRange([200, 220], 'metric')).toBe('183-201 m');
  });
});

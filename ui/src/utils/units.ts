/**
 * Display unit presets. `mphMeters` is the teaching-pro mix: speeds stay in
 * mph (the industry standard even in metric countries) while carry is shown
 * in meters.
 */
export type UnitSystem = 'imperial' | 'metric' | 'mphMeters';

type SpeedUnit = 'mph' | 'km/h';
type DistanceUnit = 'yds' | 'm';

const UNIT_SYSTEMS: Record<UnitSystem, { speed: SpeedUnit; distance: DistanceUnit }> = {
  imperial: { speed: 'mph', distance: 'yds' },
  metric: { speed: 'km/h', distance: 'm' },
  mphMeters: { speed: 'mph', distance: 'm' },
};

export const UNIT_SYSTEM_IDS = Object.keys(UNIT_SYSTEMS) as UnitSystem[];

const MPH_TO_KMH = 1.60934;
const YARDS_TO_METERS = 0.9144;

export function isUnitSystem(value: unknown): value is UnitSystem {
  return typeof value === 'string' && Object.hasOwn(UNIT_SYSTEMS, value);
}

export function convertSpeedFromMph(speedMph: number, unitSystem: UnitSystem): number {
  if (getSpeedUnit(unitSystem) === 'km/h') {
    return speedMph * MPH_TO_KMH;
  }

  return speedMph;
}

export function convertDistanceFromYards(distanceYards: number, unitSystem: UnitSystem): number {
  if (getDistanceUnit(unitSystem) === 'm') {
    return distanceYards * YARDS_TO_METERS;
  }

  return distanceYards;
}

export function formatSpeed(speedMph: number, unitSystem: UnitSystem, digits = 1): string {
  return convertSpeedFromMph(speedMph, unitSystem).toFixed(digits);
}

export function formatDistance(distanceYards: number, unitSystem: UnitSystem, digits = 0): string {
  return convertDistanceFromYards(distanceYards, unitSystem).toFixed(digits);
}

export function getSpeedUnit(unitSystem: UnitSystem): SpeedUnit {
  return UNIT_SYSTEMS[unitSystem].speed;
}

export function getDistanceUnit(unitSystem: UnitSystem): DistanceUnit {
  return UNIT_SYSTEMS[unitSystem].distance;
}

export function getUnitsLabel(unitSystem: UnitSystem): string {
  return `${getSpeedUnit(unitSystem)} / ${getDistanceUnit(unitSystem)}`;
}

export function formatCarryRange(carryRange: [number, number], unitSystem: UnitSystem): string {
  const min = formatDistance(carryRange[0], unitSystem, 0);
  const max = formatDistance(carryRange[1], unitSystem, 0);
  return `${min}-${max} ${getDistanceUnit(unitSystem)}`;
}

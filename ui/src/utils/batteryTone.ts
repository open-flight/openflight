export type BatteryTone = 'ok' | 'warn' | 'crit' | 'unknown';

/** Green above 50%, amber from 21-50%, red at 20% and below (backend low). */
export function batteryTone(percent: number | null): BatteryTone {
  if (percent === null) return 'unknown';
  if (percent <= 20) return 'crit';
  if (percent <= 50) return 'warn';
  return 'ok';
}

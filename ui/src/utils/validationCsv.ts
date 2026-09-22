import type { Shot } from '../types/shot';
import { getSwingSpeedMph, isSwingSpeedShot } from '../types/shot';
import { getEmptyValidationEntry, type ValidationEntry } from '../stores/useValidationStore';

function csvValue(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return '';
  const text = String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** OpenFlight speed for a shot in the unit the CSV records (always mph). */
export function openflightSpeedMph(shot: Shot): number {
  return isSwingSpeedShot(shot) ? getSwingSpeedMph(shot) : shot.ball_speed_mph;
}

/** Signed difference against the comparator device, or null if none entered. */
export function comparatorDifference(shot: Shot, entry: ValidationEntry): number | null {
  const comparatorSpeed = Number.parseFloat(entry.comparatorSpeed);
  return Number.isFinite(comparatorSpeed) ? openflightSpeedMph(shot) - comparatorSpeed : null;
}

export function buildValidationCsv(shots: Shot[], entries: Record<string, ValidationEntry>): string {
  const headers = [
    'shot_number',
    'timestamp',
    'profile',
    'mode',
    'implement',
    'openflight_speed_mph',
    'comparator_device',
    'comparator_speed_mph',
    'difference_mph',
    'reading_count',
    'trigger_speed_mph',
    'duration_ms',
    'peak_magnitude',
    'notes',
  ];

  const rows = shots.map((shot, index) => {
    const validation = entries[shot.timestamp] ?? getEmptyValidationEntry();
    const difference = comparatorDifference(shot, validation);

    return [
      index + 1,
      shot.timestamp,
      shot.profile_name ?? '',
      shot.mode ?? '',
      shot.training_implement_label ?? shot.club,
      openflightSpeedMph(shot).toFixed(1),
      validation.comparatorDevice,
      validation.comparatorSpeed,
      difference === null ? '' : difference.toFixed(1),
      shot.swing_speed_reading_count ?? '',
      shot.swing_speed_trigger_mph ?? '',
      shot.swing_speed_duration_ms ?? '',
      shot.peak_magnitude ?? '',
      validation.notes,
    ].map(csvValue);
  });

  return [headers.map(csvValue), ...rows].map((row) => row.join(',')).join('\n');
}

export function downloadCsv(filename: string, content: string) {
  const blob = new Blob([content], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

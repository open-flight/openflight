import type { Profile } from '../types/profile';
import { ALL_CLUBS, getClubName } from './clubs';

/**
 * One physical club in a profile's bag. Mirrors `openflight.clubs.bag.BagClub`;
 * several entries may share a `club` type (two 56° sand wedges on test).
 */
export interface BagClub {
  id: string;
  /** Built-in club type id, e.g. "sw". Drives physics and simulators. */
  club: string;
  brand: string;
  model: string;
  loft_deg: number | null;
  bounce_deg: number | null;
  lie_deg: number | null;
  shaft: string;
  notes: string;
}

// Limits mirror src/openflight/clubs/bag.py. The server re-validates every save.
export const MAX_BAG_CLUBS = 30;
export const BAG_TEXT_MAX = 40;
export const BAG_NOTES_MAX = 120;

export const BAG_ANGLE_RANGES = {
  loft_deg: { min: 0, max: 80 },
  bounce_deg: { min: -10, max: 30 },
  lie_deg: { min: 40, max: 80 },
} as const;

export type BagAngleField = keyof typeof BAG_ANGLE_RANGES;
export type BagTextField = 'brand' | 'model' | 'shaft' | 'notes';

const CLUB_IDS = new Set(ALL_CLUBS.map((club) => club.id));

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function angle(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

/**
 * The profile's bag from its settings, skipping anything malformed. The
 * server only stores validated bags, so this guards against old or
 * hand-edited data rather than enforcing rules.
 */
export function profileBag(profile: Profile | null | undefined): BagClub[] {
  const raw = profile?.settings?.bag;
  if (!Array.isArray(raw)) return [];
  const bag: BagClub[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== 'object') continue;
    const record = entry as Record<string, unknown>;
    const id = text(record.id);
    const club = text(record.club);
    if (!id || !CLUB_IDS.has(club)) continue;
    bag.push({
      id,
      club,
      brand: text(record.brand),
      model: text(record.model),
      loft_deg: angle(record.loft_deg),
      bounce_deg: angle(record.bounce_deg),
      lie_deg: angle(record.lie_deg),
      shaft: text(record.shaft),
      notes: text(record.notes),
    });
  }
  return bag;
}

export function findBagClub(bag: ReadonlyArray<BagClub>, id: string): BagClub | null {
  if (!id) return null;
  return bag.find((club) => club.id === id) ?? null;
}

/** "56°", "56.5°", or "" when not recorded. */
export function formatDegrees(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '';
  return `${Number(value.toFixed(1))}°`;
}

/** Brand and model, e.g. "Acme Spinner"; "" when neither is set. */
export function bagClubMakeModel(club: Pick<BagClub, 'brand' | 'model'>): string {
  return [club.brand, club.model].filter(Boolean).join(' ');
}

/** The short tile label for the club type, e.g. "SW". */
export function clubShortLabel(clubId: string): string {
  return ALL_CLUBS.find((club) => club.id === clubId)?.label ?? clubId.toUpperCase();
}

/**
 * Header text for a bag club: "Sand Wedge · 56° · Spinner". The model (or the
 * brand when there is no model) tells two clubs of the same loft apart.
 */
export function bagClubDisplayName(club: BagClub): string {
  return [getClubName(club.club), formatDegrees(club.loft_deg), club.model || club.brand].filter(Boolean).join(' · ');
}

/** Compact label for chips and list rows: "SW 56° Spinner". */
export function bagClubCompactLabel(club: BagClub): string {
  return [clubShortLabel(club.club), formatDegrees(club.loft_deg), club.model || club.brand].filter(Boolean).join(' ');
}

/** Specs line for the bag editor: "56° loft · 10° bounce · 64° lie". */
export function bagClubSpecs(club: BagClub, labels: Record<BagAngleField, string>): string {
  return (Object.keys(BAG_ANGLE_RANGES) as BagAngleField[])
    .filter((field) => club[field] !== null)
    .map((field) => `${formatDegrees(club[field])} ${labels[field]}`)
    .join(' · ');
}

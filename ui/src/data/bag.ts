import { ALL_CLUBS, getClubName } from './clubs';

/**
 * One physical club in a bag. Mirrors `openflight.clubs.club.CustomClub`: it
 * inherits `name`, `label` and `loft_deg` from its built-in club type, and
 * several clubs may share a type (two 56° sand wedges on test).
 */
export interface CustomClub {
  /** Issued by the server; stable across edits and never reused. */
  id: string;
  /** The bag the club belongs to: today, its profile's id. */
  bag: string;
  /** Built-in club type id, e.g. "sw". Drives physics and simulators. */
  club: string;
  name: string;
  label: string;
  /** The club's own loft, or its type's nominal loft when none was recorded. */
  loft_deg: number;
  brand: string;
  model: string;
  bounce_deg: number | null;
  lie_deg: number | null;
  shaft: string;
  /** Which hand the club is built for; null when not recorded. */
  hand: BagHand | null;
  notes: string;
}

/** What `save_custom_club` sends. No `id` adds a club; a null loft inherits the type's. */
export type CustomClubPayload = Pick<CustomClub, 'club' | 'brand' | 'model' | 'shaft' | 'notes' | 'hand'> & {
  id?: string;
  bag?: string;
  loft_deg: number | null;
  bounce_deg: number | null;
  lie_deg: number | null;
};

/** The Socket.IO `clubs` event: every bag's clubs, plus each type's nominal loft. */
export interface ClubsSnapshot {
  clubs: CustomClub[];
  lofts: Record<string, number>;
}

// Limits mirror src/openflight/clubs/{club,store}.py. The server re-validates every save.
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
export type BagHand = 'right' | 'left';
export const BAG_HANDS: readonly BagHand[] = ['right', 'left'];
/** Words for the editor's specs line, e.g. { loft_deg: 'loft', left: 'Left-handed' }. */
export type BagSpecLabels = Record<BagAngleField | BagHand, string>;

const CLUB_IDS = new Set(ALL_CLUBS.map((club) => club.id));

function text(value: unknown): string {
  return typeof value === 'string' ? value : '';
}

function hand(value: unknown): BagHand | null {
  return value === 'right' || value === 'left' ? value : null;
}

function angle(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
}

function parseCustomClub(entry: unknown): CustomClub | null {
  if (!entry || typeof entry !== 'object') return null;
  const record = entry as Record<string, unknown>;
  const id = text(record.id);
  const bag = text(record.bag);
  const club = text(record.club);
  const loft = angle(record.loft_deg);
  if (!id || !bag || !CLUB_IDS.has(club) || loft === null) return null;
  return {
    id,
    bag,
    club,
    name: text(record.name) || getClubName(club),
    label: text(record.label) || clubShortLabel(club),
    loft_deg: loft,
    brand: text(record.brand),
    model: text(record.model),
    bounce_deg: angle(record.bounce_deg),
    lie_deg: angle(record.lie_deg),
    shaft: text(record.shaft),
    hand: hand(record.hand),
    notes: text(record.notes),
  };
}

/**
 * The `clubs` event, skipping anything malformed. The server only stores
 * validated clubs, so this guards against a mismatched server rather than
 * enforcing rules.
 */
export function parseClubsSnapshot(raw: unknown): ClubsSnapshot {
  const record = raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  const clubs = Array.isArray(record.clubs) ? record.clubs.map(parseCustomClub) : [];
  const lofts: Record<string, number> = {};
  if (record.lofts && typeof record.lofts === 'object') {
    for (const [club, value] of Object.entries(record.lofts as Record<string, unknown>)) {
      const loft = angle(value);
      if (CLUB_IDS.has(club) && loft !== null) lofts[club] = loft;
    }
  }
  return { clubs: clubs.filter((club): club is CustomClub => club !== null), lofts };
}

/** One bag's clubs, in the order they were added. */
export function bagClubs(clubs: ReadonlyArray<CustomClub>, bag: string): CustomClub[] {
  return bag ? clubs.filter((club) => club.bag === bag) : [];
}

export function findCustomClub(clubs: ReadonlyArray<CustomClub>, id: string): CustomClub | null {
  if (!id) return null;
  return clubs.find((club) => club.id === id) ?? null;
}

/** "56°", "56.5°", or "" when not recorded. */
export function formatDegrees(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '';
  return `${Number(value.toFixed(1))}°`;
}

/** Brand and model, e.g. "Acme Spinner"; "" when neither is set. */
export function customClubMakeModel(club: Pick<CustomClub, 'brand' | 'model'>): string {
  return [club.brand, club.model].filter(Boolean).join(' ');
}

/** The short tile label for the club type, e.g. "SW". */
export function clubShortLabel(clubId: string): string {
  return ALL_CLUBS.find((club) => club.id === clubId)?.label ?? clubId.toUpperCase();
}

/**
 * Header text for a custom club: "Sand Wedge · 56° · Spinner". The model (or
 * the brand when there is no model) tells two clubs of the same loft apart.
 */
export function customClubDisplayName(club: CustomClub): string {
  return [getClubName(club.club), formatDegrees(club.loft_deg), club.model || club.brand].filter(Boolean).join(' · ');
}

/** Compact label for chips and list rows: "SW 56° Spinner". */
export function customClubCompactLabel(club: CustomClub): string {
  return [clubShortLabel(club.club), formatDegrees(club.loft_deg), club.model || club.brand].filter(Boolean).join(' ');
}

/** Specs line for the bag editor: "56° loft · 10° bounce · 64° lie · Left-handed". */
export function customClubSpecs(club: CustomClub, labels: BagSpecLabels): string {
  const angles = (Object.keys(BAG_ANGLE_RANGES) as BagAngleField[])
    .filter((field) => club[field] !== null)
    .map((field) => `${formatDegrees(club[field])} ${labels[field]}`);
  return [...angles, club.hand ? labels[club.hand] : ''].filter(Boolean).join(' · ');
}

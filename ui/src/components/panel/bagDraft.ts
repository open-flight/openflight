import {
  BAG_ANGLE_RANGES,
  BAG_NOTES_MAX,
  BAG_TEXT_MAX,
  type BagAngleField,
  type BagHand,
  type BagTextField,
  type CustomClub,
  type CustomClubPayload,
} from '../../data/bag';

/** Every editable field of a custom club, in form order. */
export const BAG_FORM_FIELDS = ['brand', 'model', 'loft_deg', 'bounce_deg', 'lie_deg', 'shaft', 'notes'] as const;
export type BagFormField = (typeof BAG_FORM_FIELDS)[number];

/**
 * The form's working copy: angles stay as typed text until saved. Club type
 * and hand are picked with buttons, not typed; '' hand is "not recorded".
 * '' id is a club not yet saved: the server issues its id.
 */
export type BagClubDraft = { id: string; club: string; hand: BagHand | '' } & Record<BagFormField, string>;

export type AngleError = 'number' | 'range';
export type DraftErrors = Partial<Record<BagAngleField, AngleError>>;

export function isAngleField(field: BagFormField): field is BagAngleField {
  return field in BAG_ANGLE_RANGES;
}

export function fieldMaxLength(field: BagFormField): number {
  if (isAngleField(field)) return 6;
  return field === 'notes' ? BAG_NOTES_MAX : BAG_TEXT_MAX;
}

export function newBagClubDraft(club: string): BagClubDraft {
  return {
    id: '',
    club,
    hand: '',
    brand: '',
    model: '',
    loft_deg: '',
    bounce_deg: '',
    lie_deg: '',
    shaft: '',
    notes: '',
  };
}

export function draftFromCustomClub(club: CustomClub): BagClubDraft {
  const angleText = (value: number | null) => (value === null ? '' : String(value));
  return {
    id: club.id,
    club: club.club,
    hand: club.hand ?? '',
    brand: club.brand,
    model: club.model,
    shaft: club.shaft,
    notes: club.notes,
    loft_deg: angleText(club.loft_deg),
    bounce_deg: angleText(club.bounce_deg),
    lie_deg: angleText(club.lie_deg),
  };
}

/**
 * Blank is "not recorded" (for loft: inherit the type's nominal loft);
 * anything else must be a number inside the field's range.
 */
export function parseAngle(raw: string, field: BagAngleField): { value: number | null } | { error: AngleError } {
  const trimmed = raw.trim();
  if (!trimmed) return { value: null };
  // Number('') and Number(' ') are 0, and Number('1e1') is 10; accept plain decimals only.
  if (!/^-?(\d+(\.\d*)?|\.\d+)$/.test(trimmed)) return { error: 'number' };
  const value = Number(trimmed);
  const { min, max } = BAG_ANGLE_RANGES[field];
  if (value < min || value > max) return { error: 'range' };
  return { value: Math.round(value * 10) / 10 };
}

export function draftErrors(draft: BagClubDraft): DraftErrors {
  const errors: DraftErrors = {};
  for (const field of Object.keys(BAG_ANGLE_RANGES) as BagAngleField[]) {
    const parsed = parseAngle(draft[field], field);
    if ('error' in parsed) errors[field] = parsed.error;
  }
  return errors;
}

function cleanText(value: string, field: BagTextField): string {
  return value.split(/\s+/).filter(Boolean).join(' ').slice(0, fieldMaxLength(field));
}

/** What to send to `save_custom_club`, or null while any angle is invalid. */
export function payloadFromDraft(draft: BagClubDraft): CustomClubPayload | null {
  const loft = parseAngle(draft.loft_deg, 'loft_deg');
  const bounce = parseAngle(draft.bounce_deg, 'bounce_deg');
  const lie = parseAngle(draft.lie_deg, 'lie_deg');
  if ('error' in loft || 'error' in bounce || 'error' in lie) return null;
  return {
    ...(draft.id ? { id: draft.id } : {}),
    club: draft.club,
    brand: cleanText(draft.brand, 'brand'),
    model: cleanText(draft.model, 'model'),
    loft_deg: loft.value,
    bounce_deg: bounce.value,
    lie_deg: lie.value,
    shaft: cleanText(draft.shaft, 'shaft'),
    notes: cleanText(draft.notes, 'notes'),
    hand: draft.hand || null,
  };
}

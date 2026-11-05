import { ALL_CLUBS, CLUBS_BY_TYPE } from '../../data/clubs';
import { clubShortLabel, formatDegrees, type CustomClub } from '../../data/bag';
import { getTrainingImplementsByGroup } from '../../data/trainingImplements';

export interface PickerOption {
  id: string;
  label: string;
  /** Second line under the label, e.g. the loft "56°". */
  detail?: string;
  /** Small third line, e.g. the model that tells two 56° wedges apart. */
  note?: string;
}

export interface PickerSection {
  name: string;
  options: ReadonlyArray<PickerOption>;
}

/** Club sections for the picker, in the order `clubs.ts` declares them. */
export function clubSections(): PickerSection[] {
  return Object.entries(CLUBS_BY_TYPE).map(([name, clubs]) => ({ name, options: clubs }));
}

/** Picker ids for built-in clubs offered alongside a bag, e.g. "club:driver". */
export const OTHER_CLUB_PREFIX = 'club:';
/** Tab holding the built-in clubs a bag does not carry. */
export const OTHER_CLUBS_SECTION = 'Other';

/**
 * A profile's bag as picker sections: the same family tabs and club order as
 * `clubSections`, with one tile per bag club. Clubs of the same type keep the
 * order the player arranged them in; families with no clubs are left out.
 * Built-in clubs the bag lacks go in a trailing "Other" tab, so a bag set up
 * only to compare two wedges never locks the player out of the driver.
 */
export function bagClubSections(bag: ReadonlyArray<CustomClub>): PickerSection[] {
  const carried = new Set(bag.map((club) => club.club));
  const families = Object.entries(CLUBS_BY_TYPE)
    .map(([name, clubs]) => ({
      name,
      options: clubs.flatMap((type) =>
        bag
          .filter((club) => club.club === type.id)
          .map((club) => ({
            id: club.id,
            label: clubShortLabel(club.club),
            detail: formatDegrees(club.loft_deg),
            note: club.model || club.brand,
          }))
      ),
    }))
    .filter((section) => section.options.length > 0);
  const others = ALL_CLUBS.filter((club) => !carried.has(club.id)).map((club) => ({
    id: `${OTHER_CLUB_PREFIX}${club.id}`,
    label: club.label,
  }));
  return others.length > 0 ? [...families, { name: OTHER_CLUBS_SECTION, options: others }] : families;
}

/** The bag picker's id for the current selection. */
export function bagPickerSelectedId(club: string, customClubId: string): string {
  return customClubId || `${OTHER_CLUB_PREFIX}${club}`;
}

/**
 * What a bag picker tile selects: a bag club (its type comes along) or a
 * built-in club from the Other tab. Null for an id the bag no longer has.
 */
export function resolveBagPickerOption(
  bag: ReadonlyArray<CustomClub>,
  id: string
): { club: string; customClubId: string } | null {
  if (id.startsWith(OTHER_CLUB_PREFIX)) {
    const club = id.slice(OTHER_CLUB_PREFIX.length);
    return ALL_CLUBS.some((entry) => entry.id === club) ? { club, customClubId: '' } : null;
  }
  const customClub = bag.find((club) => club.id === id);
  return customClub ? { club: customClub.club, customClubId: customClub.id } : null;
}

/** Training-implement sections, used in place of clubs during swing-speed mode. */
export function trainingImplementSections(): PickerSection[] {
  return Object.entries(getTrainingImplementsByGroup()).map(([name, items]) => ({
    name,
    options: items.map((item) => ({ id: item.id, label: item.label })),
  }));
}

/** Open on the family that already contains the selection (driver → Woods). */
export function initialPickerSection(sections: ReadonlyArray<PickerSection>, selectedId: string): string {
  const match = sections.find((section) => section.options.some((option) => option.id === selectedId));
  return match?.name ?? sections[0]?.name ?? '';
}

export const PICKER_COLUMNS = 4;

/** Rows needed to show the densest tab at `PICKER_COLUMNS` across, so tile size stays stable. */
export function pickerGridRows(sections: ReadonlyArray<PickerSection>, columns = PICKER_COLUMNS): number {
  let maxRows = 1;
  for (const section of sections) {
    const rows = Math.ceil(section.options.length / columns);
    if (rows > maxRows) {
      maxRows = rows;
    }
  }
  return maxRows;
}

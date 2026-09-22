export interface Club {
  id: string;
  /** Compact picker tile, e.g. "7i". */
  label: string;
  /** Prose name for the header, e.g. "7 Iron". */
  name: string;
}

// Clubs grouped by type. Object insertion order is preserved and drives the
// display order in both the ClubPicker dropdown and the ClubSelectScreen.
export const CLUBS_BY_TYPE: Record<string, Club[]> = {
  Irons: [
    { id: '2-iron', label: '2i', name: '2 Iron' },
    { id: '3-iron', label: '3i', name: '3 Iron' },
    { id: '4-iron', label: '4i', name: '4 Iron' },
    { id: '5-iron', label: '5i', name: '5 Iron' },
    { id: '6-iron', label: '6i', name: '6 Iron' },
    { id: '7-iron', label: '7i', name: '7 Iron' },
    { id: '8-iron', label: '8i', name: '8 Iron' },
    { id: '9-iron', label: '9i', name: '9 Iron' },
    { id: 'pw', label: 'PW', name: 'Pitching Wedge' },
    { id: 'gw', label: 'GW', name: 'Gap Wedge' },
    { id: 'sw', label: 'SW', name: 'Sand Wedge' },
    { id: 'lw', label: 'LW', name: 'Lob Wedge' },
  ],
  Hybrids: [
    { id: '3-hybrid', label: '3H', name: '3 Hybrid' },
    { id: '5-hybrid', label: '5H', name: '5 Hybrid' },
    { id: '7-hybrid', label: '7H', name: '7 Hybrid' },
    { id: '9-hybrid', label: '9H', name: '9 Hybrid' },
  ],
  Woods: [
    { id: 'driver', label: 'DR', name: 'Driver' },
    { id: '3-wood', label: '3W', name: '3 Wood' },
    { id: '5-wood', label: '5W', name: '5 Wood' },
    { id: '7-wood', label: '7W', name: '7 Wood' },
  ],
};

export const ALL_CLUBS: Club[] = Object.values(CLUBS_BY_TYPE).flat();

export function getClubName(clubId: string): string {
  return ALL_CLUBS.find((club) => club.id === clubId)?.name ?? clubId;
}

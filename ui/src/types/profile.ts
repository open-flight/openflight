/** One named context shots are attributed to: a person or a place. */
export interface Profile {
  id: string;
  name: string;
  created_at: string;
  /** Open bag the server round-trips untouched; later features claim keys here. */
  settings: Record<string, unknown>;
}

/** The server's authoritative roster + selection, sent as one event. */
export interface ProfilesSnapshot {
  profiles: Profile[];
  active_profile_id: string;
}

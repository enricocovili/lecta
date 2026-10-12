// The roles of a user, as the UI names them (decision 121).
export type Role = "viewer" | "editor" | "admin";

export const ROLE_LABEL: Record<Role, string> = { viewer: "Lettore", editor: "Autore", admin: "Amministratore" };

/** What the role lets one do, in a line (the invite page). */
export const ROLE_HINT: Record<Role, string> = {
  viewer: "Leggi tutte le materie e le lezioni, senza modificarle.",
  editor: "Crei e modifichi le tue materie e leggi quelle pubblicate dagli altri.",
  admin: "Gestisci tutto: le materie di tutti, la pubblicazione, le impostazioni e gli utenti.",
};

/** `/api/auth/me`: the middleware loads it for every private page (`Astro.locals.auth`). */
export interface Me {
  authenticated: boolean;
  username?: string;
  user_id?: number;
  role?: Role;
  /** False for invited users, who sign in only with passkeys: no password to change, no 2FA. */
  has_password?: boolean;
  totp_enabled?: boolean;
  csrf?: string;
  needs_setup?: boolean;
}

// What the menus show to each role (decision 124). The backend checks every call by itself: these only hide what
// would answer 403 anyway.
export const isAdmin = (role?: Role) => role === "admin";
/** Creating courses and lessons: editors and admins. */
export const canCreate = (role?: Role) => role === "editor" || role === "admin";

/** The owner of a course someone else owns, to show as «di <owner>»; null for one's own course (decision 125). */
export const otherOwner = (c: { owner_id?: number | null; owner?: string | null }, userId?: number) =>
  c.owner && c.owner_id !== userId ? c.owner : null;

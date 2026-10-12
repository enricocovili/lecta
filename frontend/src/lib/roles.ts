// The roles of a user, as the UI names them (decision 121).
export type Role = "viewer" | "editor" | "admin";

export const ROLE_LABEL: Record<Role, string> = { viewer: "Lettore", editor: "Autore", admin: "Amministratore" };

/** What the role lets one do, in a line (the invite page). */
export const ROLE_HINT: Record<Role, string> = {
  viewer: "Leggi tutte le materie e le lezioni, senza modificarle.",
  editor: "Crei e modifichi le tue materie e leggi quelle pubblicate dagli altri.",
  admin: "Gestisci tutto: le materie di tutti, la pubblicazione, le impostazioni e gli utenti.",
};

// The files of a lab on the page: their shape, the tree they make, and what a drop or a picker hands over (with folders).
import type { LabComment } from "./useLabComments";

export type LabFileKind = "text" | "notebook" | "pdf" | "image" | "binary";

export interface LabFile {
  id: number;
  path: string;
  kind: LabFileKind;
  language: string | null;
  size: number;
  version: number;
  updated_at: string;
}

export interface LabData {
  id: number;
  lesson: { id: number; number: number; title: string; course_id: number; course_name: string; chapter: { id: number; title: string } | null };
  files: LabFile[];
  comments: LabComment[];
}

/** owner: the signed-in author; write: a share link that can edit; read: a share link that only looks. */
export type LabAccess = "owner" | "write" | "read";

export const MAX_FILE_BYTES = 20 * 1024 * 1024;

export interface Folder {
  name: string;
  path: string;
  folders: Folder[];
  files: LabFile[];
}

/** The folders and files of the lab, folders first, by name. */
export function tree(files: LabFile[]): Folder {
  const root: Folder = { name: "", path: "", folders: [], files: [] };
  for (const f of files) {
    const parts = f.path.split("/");
    let at = root;
    for (const part of parts.slice(0, -1)) {
      const path = at.path ? `${at.path}/${part}` : part;
      let next = at.folders.find((x) => x.name === part);
      if (!next) {
        next = { name: part, path, folders: [], files: [] };
        at.folders.push(next);
      }
      at = next;
    }
    at.files.push(f);
  }
  const sort = (d: Folder) => {
    d.folders.sort((a, b) => a.name.localeCompare(b.name));
    d.files.sort((a, b) => a.path.localeCompare(b.path));
    d.folders.forEach(sort);
  };
  sort(root);
  return root;
}

export function baseName(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

export interface Picked {
  file: File;
  path: string;
}

/** Files from an `<input type=file>` (with `webkitdirectory` they keep their folders). */
export function fromInput(list: FileList | null): Picked[] {
  return [...(list ?? [])].map((file) => ({ file, path: file.webkitRelativePath || file.name }));
}

/** Files from a drop: loose files and whole folders (read through the entries API, folders kept in the names). */
export async function fromDrop(dt: DataTransfer): Promise<Picked[]> {
  const entries = [...dt.items].map((i) => (i.kind === "file" ? i.webkitGetAsEntry?.() : null)).filter((e): e is FileSystemEntry => !!e);
  if (!entries.length) return [...dt.files].map((file) => ({ file, path: file.name }));
  const out: Picked[] = [];
  const walk = async (entry: FileSystemEntry, prefix: string): Promise<void> => {
    if (entry.isFile) {
      const file = await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej));
      out.push({ file, path: prefix + entry.name });
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader();
      // readEntries hands the content over in batches, until an empty one.
      for (;;) {
        const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej));
        if (!batch.length) break;
        for (const e of batch) await walk(e, `${prefix}${entry.name}/`);
      }
    }
  };
  for (const e of entries) await walk(e, "");
  return out;
}

/** Hidden files and the clutter of editors and builds are left out of a folder. */
export function worthUploading(path: string): boolean {
  return !path.split("/").some((p) => p.startsWith(".") || p === "__pycache__" || p === "node_modules" || p === "__MACOSX");
}

export function fileIcon(f: LabFile): string {
  return f.kind === "image" ? "image" : f.kind === "pdf" ? "file-text" : f.kind === "binary" ? "archive" : "code";
}

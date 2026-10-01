// Which camera folder a file came from.
//
// The drives group a trip by where its files came from —
// Trips/<trip>/{xt5, canon, iphone, google photos} — so when a project is
// opened on the whole trip, the first path segment below it is the device.
// Empty when the file sits directly in the project folder, or when the project
// is one camera folder already: there every photo would answer the same thing,
// so there is nothing worth showing.
export function deviceOf(path: string, folder?: string | null): string {
  if (!folder) return "";
  const base = folder.endsWith("/") ? folder : `${folder}/`;
  if (!path.startsWith(base)) return "";
  const rest = path.slice(base.length);
  const cut = rest.indexOf("/");
  return cut === -1 ? "" : rest.slice(0, cut);
}

// Every device present, in the order the photos run.
export function devicesIn(paths: string[], folder?: string | null): string[] {
  const seen: string[] = [];
  for (const p of paths) {
    const d = deviceOf(p, folder);
    if (d && !seen.includes(d)) seen.push(d);
  }
  return seen;
}

function folderName(folder: string): string {
  const trimmed = folder.endsWith("/") ? folder.slice(0, -1) : folder;
  const cut = trimmed.lastIndexOf("/");
  return cut === -1 ? trimmed : trimmed.slice(cut + 1);
}

// The source a file belongs to: the top-level folder under the project.
// A file sitting directly in the project folder is one source too, named by
// that folder — unlike `deviceOf`, which returns nothing there because a
// badge would only repeat the project. Empty when the path is outside the
// project, so a list that cannot be split is left alone.
export function sourceOf(path: string, folder?: string | null): string {
  if (!folder) return "";
  const base = folder.endsWith("/") ? folder : `${folder}/`;
  if (!path.startsWith(base)) return "";
  const rest = path.slice(base.length);
  const cut = rest.indexOf("/");
  return cut === -1 ? folderName(folder) : rest.slice(0, cut);
}

// Distinct sources, in the order the paths are given.
export function sourcesIn(paths: string[], folder?: string | null): string[] {
  const seen: string[] = [];
  for (const p of paths) {
    const s = sourceOf(p, folder);
    if (s && !seen.includes(s)) seen.push(s);
  }
  return seen;
}

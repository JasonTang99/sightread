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

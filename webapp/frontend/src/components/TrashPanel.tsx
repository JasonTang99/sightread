import { useEffect, useState } from "react";

interface Props {
  pendingCount: number;
  onRefresh: () => Promise<void>;
  onError: (msg: string) => void;
}

function imgUrl(path: string, w = 200) {
  return `/api/image?path=${encodeURIComponent(path)}&w=${w}`;
}

export function TrashPanel({ pendingCount, onRefresh, onError }: Props) {
  const [open, setOpen] = useState(false);
  const [paths, setPaths] = useState<string[]>([]);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [restoring, setRestoring] = useState(false);

  const loadTrash = async () => {
    try {
      const res = await fetch("/api/trash");
      if (!res.ok) return;
      const data = await res.json();
      setPaths(data.paths);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    }
  };

  useEffect(() => {
    if (open) loadTrash();
  }, [open, pendingCount]);

  const toggleSelect = (path: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  };

  const restore = async () => {
    if (selected.size === 0) return;
    setRestoring(true);
    try {
      const res = await fetch("/api/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ paths: Array.from(selected) }),
      });
      if (!res.ok) throw new Error(`Restore failed: ${res.status}`);
      setSelected(new Set());
      await loadTrash();
      await onRefresh();
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setRestoring(false);
    }
  };

  if (pendingCount === 0) return null;

  return (
    <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="w-full px-4 py-3 text-left flex items-center justify-between hover:bg-gray-50 transition-colors"
      >
        <span className="text-sm font-medium text-gray-700">
          🗑️ Trash — {pendingCount} pending deletion
        </span>
        <span className="text-gray-400 text-xs">{open ? "▲ collapse" : "▼ expand"}</span>
      </button>

      {open && (
        <div className="border-t border-gray-100 px-4 py-4 space-y-3">
          <p className="text-xs text-gray-500">
            These will be deleted when you run <code className="bg-gray-100 px-1 py-0.5 rounded">scripts/delete_marked.py</code>. Check to restore.
          </p>

          {paths.length === 0 ? (
            <p className="text-sm text-gray-400">Loading…</p>
          ) : (
            <>
              <div className="grid gap-2" style={{ gridTemplateColumns: "repeat(6, minmax(0, 1fr))" }}>
                {paths.map((p) => (
                  <div key={p} className={`rounded border overflow-hidden cursor-pointer transition-all ${selected.has(p) ? "border-blue-400 ring-2 ring-blue-200" : "border-gray-200"}`} onClick={() => toggleSelect(p)}>
                    <img
                      src={imgUrl(p)}
                      alt=""
                      className="w-full object-cover h-20 bg-gray-100"
                      onError={(e) => { (e.target as HTMLImageElement).style.display = "none"; }}
                    />
                    <div className="px-1 py-1 text-center">
                      <p className="text-xs text-gray-400 truncate" title={p}>{p.split("/").pop()}</p>
                      {selected.has(p) && <p className="text-xs text-blue-600 font-medium">restore</p>}
                    </div>
                  </div>
                ))}
              </div>

              {selected.size > 0 && (
                <button
                  onClick={restore}
                  disabled={restoring}
                  className="px-4 py-1.5 text-sm font-medium bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                >
                  {restoring ? "Restoring…" : `Restore ${selected.size} selected`}
                </button>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

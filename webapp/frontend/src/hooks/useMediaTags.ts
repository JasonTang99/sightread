import { useCallback, useEffect, useState } from "react";
import type { VideoTagsState } from "../types";

export function useMediaTags(
  videoTags: VideoTagsState,
  onVideoTagsChange: ((tags: VideoTagsState) => void) | undefined,
  favorites: string[],
  onToggleFavorite: ((path: string) => Promise<void>) | undefined,
  onError: (msg: string) => void,
  emptyFlash: string,
) {
  const [busy, setBusy] = useState(false);
  const [flash, setFlash] = useState<string | null>(null);

  useEffect(() => {
    if (flash === null) return;
    const t = setTimeout(() => setFlash(null), 1600);
    return () => clearTimeout(t);
  }, [flash]);

  const apply = useCallback(
    async (body: { tags?: string[]; assign?: Record<string, string | null> }) => {
      setBusy(true);
      try {
        const res = await fetch("/api/video-tags", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!res.ok) {
          const detail = await res.json().catch(() => null);
          throw new Error(detail?.detail ?? `Tag update failed: ${res.status}`);
        }
        const data = await res.json();
        onVideoTagsChange?.({ tags: data.tags ?? [], assignments: data.assignments ?? {} });
        return true;
      } catch (e) {
        onError(e instanceof Error ? e.message : String(e));
        return false;
      } finally {
        setBusy(false);
      }
    },
    [onError, onVideoTagsChange],
  );

  // A tag means "deliver this into …/<tag>/". For videos that only happens
  // when the shot is starred; photos export either way, but starring is still
  // the keep. Clearing a tag never stars.
  const assignTag = useCallback(
    async (path: string, tag: string | null) => {
      if (tag !== null && !favorites.includes(path) && onToggleFavorite) {
        try {
          await onToggleFavorite(path);
        } catch (e) {
          onError(e instanceof Error ? e.message : String(e));
          return;
        }
      }
      const ok = await apply({ assign: { [path]: tag } });
      if (ok) setFlash(tag ?? emptyFlash);
    },
    [apply, favorites, onToggleFavorite, onError, emptyFlash],
  );

  const addTag = useCallback(async () => {
    const name = window.prompt("Tag name (export subfolder):");
    if (!name?.trim()) return;
    const next = [...videoTags.tags];
    if (!next.includes(name.trim())) next.push(name.trim());
    await apply({ tags: next });
  }, [apply, videoTags.tags]);

  const nextTag = useCallback(
    (cur: string | null): string | null => {
      const { tags } = videoTags;
      if (tags.length === 0) return null;
      if (!cur) return tags[0];
      const i = tags.indexOf(cur);
      if (i < 0 || i === tags.length - 1) return null;
      return tags[i + 1];
    },
    [videoTags],
  );

  return { assignTag, addTag, nextTag, busy, flash, clearFlash: () => setFlash(null) };
}

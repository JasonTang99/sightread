interface Props {
  tags: string[];
  currentTag: string | null;
  isFavorited: boolean;
  busy: boolean;
  flash: string | null;
  showSlotKeys?: boolean;
  untaggedLabel: string;
  untaggedTitle: string;
  emptyBadgeTitle?: string;
  onAssign: (tag: string | null) => void;
}

export function TagBar({
  tags,
  currentTag,
  isFavorited,
  busy,
  flash,
  showSlotKeys = true,
  untaggedLabel,
  untaggedTitle,
  emptyBadgeTitle,
  onAssign,
}: Props) {
  return (
    <>
      {isFavorited && (
        <span
          className={`text-xs font-medium px-2 py-0.5 rounded shrink-0 transition-colors ${
            flash !== null
              ? "bg-violet-600 text-white"
              : currentTag
                ? "bg-violet-100 text-violet-800"
                : "bg-gray-100 text-gray-500"
          }`}
          title={currentTag ? `Exports to …/${currentTag}/` : (emptyBadgeTitle ?? `Exports to …/${untaggedLabel}/`)}
        >
          {flash !== null ? `✓ ${flash}` : `🏷 ${currentTag ?? untaggedLabel}`}
        </span>
      )}
      <div className="flex items-center gap-1 shrink-0 max-w-md overflow-x-auto">
        {isFavorited && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onAssign(null)}
            className={`text-[10px] px-1.5 py-0.5 rounded border ${
              !currentTag
                ? "bg-violet-600 border-violet-600 text-white font-semibold"
                : "border-gray-200 text-gray-500 hover:bg-gray-50"
            }`}
            title={untaggedTitle}
          >{!currentTag && <span className="mr-0.5">✓</span>}—</button>
        )}
        {tags.map((tag, i) => {
          const isCurrent = isFavorited && currentTag === tag;
          const slot = showSlotKeys && i < 9;
          const dest = slot ? `Tag ${i + 1} (${i + 1}) → …/${tag}/` : `Export to …/${tag}/`;
          return (
            <button
              key={tag}
              type="button"
              disabled={busy}
              onClick={() => onAssign(tag)}
              className={`text-[10px] px-1.5 py-0.5 rounded border whitespace-nowrap ${
                isCurrent
                  ? "bg-violet-600 border-violet-600 text-white font-semibold"
                  : isFavorited
                    ? "border-gray-200 text-gray-600 hover:bg-gray-50"
                    : "border-gray-200 text-gray-400 hover:bg-gray-50 hover:text-gray-600"
              }`}
              title={isFavorited ? dest : `${dest} — also stars`}
            >
              {isCurrent
                ? <span className="mr-0.5">✓</span>
                : slot && <span className="font-bold mr-0.5">{i + 1}</span>}
              {tag}
            </button>
          );
        })}
      </div>
    </>
  );
}

import { useState } from "react";

interface Props {
  // The Live Photo's motion file, as the pipeline recorded it.
  motion: string;
  // Match the still it covers: tiles crop, the review views letterbox.
  fit?: "cover" | "contain";
  // Where the badge sits; each view already has chrome in some corners.
  corner?: string;
}

// The LIVE badge for a still that has a motion file, and the playback behind
// it. Playing is on the badge rather than the whole tile so that moving the
// pointer across a grid, or towards the keep button, does not set dozens of
// clips going. The <video> is only mounted while hovered: a media element per
// tile would load eagerly and, at six connections per origin, starve the lazy
// thumbnails around it. The server has already queued a browser-playable
// transcode of each motion file, since most phones record them in HEVC.
export function LiveMotion({ motion, fit = "cover", corner = "top-1.5 left-1.5" }: Props) {
  const [playing, setPlaying] = useState(false);
  return (
    <>
      {playing && (
        <video
          src={`/api/video?path=${encodeURIComponent(motion)}`}
          className={`absolute inset-0 w-full h-full pointer-events-none ${
            fit === "cover" ? "object-cover" : "object-contain"
          }`}
          autoPlay
          muted
          loop
          playsInline
        />
      )}
      <span
        className={`absolute ${corner} z-10 bg-black/60 text-white text-[10px] font-semibold tracking-wider px-1.5 py-0.5 rounded-full select-none cursor-default`}
        onMouseEnter={() => setPlaying(true)}
        onMouseLeave={() => setPlaying(false)}
        // The tile beneath toggles keep/delete on click; the badge is not a vote.
        onClick={(e) => e.stopPropagation()}
        title="Live Photo — hover to play. Deleted and exported with the photo."
        data-testid="live-badge"
      >
        LIVE
      </span>
    </>
  );
}

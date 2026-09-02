"""Publish a trip's deliverables into an exports tree the editor opens.

Curation decides what is worth keeping; this puts it somewhere an editor will
look, grouped per trip:

    <EXPORTS_ROOT>/<project folder name>/DSCF4539.JPG
    <EXPORTS_ROOT>/<project folder name>/<tag or "untagged">/DSCF4601.MOV

Three rules shape the rest of it.

**Every surviving photo, as a JPEG.** Stars pick what to cut with, not what to
keep, so the export delivers every photo the trip still has — anything not
marked for deletion — and the raw and `.xmp` sidecars stay behind in the import
folder. Videos are the exception: footage is delivered only when it was
starred, because a trip holds far more of it than an edit ever uses, and
starring is the only pass that has looked at it.

**Originals only.** Never the derived caches — `thumb_cache` holds resized
JPEGs and `video_cache` holds a 1440p bitrate-capped transcode built for
scrubbing, not for editing. This follows the rule clip export already states.

**Links, not copies, where the filesystem allows it.** The import tree and the
exports tree are normally the same drive, so a hardlink delivers the file for
zero bytes and no wait. A hardlink is not a shortcut that can dangle: the data
stays alive until the last name for it goes, so an export survives whatever
happens to the import folder afterwards. Only a cross-filesystem exports root
falls back to copying.

Nothing here is destructive: it only ever writes into the exports tree, and it
never overwrites an existing file.
"""
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from clips import EXPORT_DIR_NAME
from projects import IMAGE_EXTENSIONS
from utils import DELETED, FAVORITE, TO_DELETE, load_decisions
from video_tags import VIDEO_EXTENSIONS, is_video, load_video_tags, sanitize_tag

# The exports root is a delivery location, not project state, so it lives
# outside the output dir and is configurable for anyone whose drives differ.
EXPORTS_ROOT = Path(os.environ.get("SIGHTREAD_EXPORTS_ROOT", "/mnt/h0/Editing/exports"))

# A partial copy that kept the final name would be indistinguishable from a
# finished one on the next run, since the skip check compares sizes. Copies land
# on this suffix and are renamed into place only once complete. Hardlinks need
# no such dance — the name appears whole or not at all.
PARTIAL_SUFFIX = ".sightread-part"

# Collision suffixes to try before giving up on a name.
MAX_NAME_ATTEMPTS = 100

# Every favourited video lands in a subfolder, tagged or not, so the trip root
# stays photos-only and an untagged clip reads as "not sorted yet" rather than
# disappearing among the stills.
UNTAGGED_DIR = "untagged"

# A photo carrying one of these is not a deliverable: one is queued for
# deletion, the other is already gone from the primary drive.
_EXCLUDED_PHOTO_STATUSES = frozenset({TO_DELETE, DELETED})


@dataclass
class ExportReport:
    """Outcome of one export run. Counts files, not shots."""

    dest: Path
    linked: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    copied_bytes: int = 0

    def as_dict(self) -> dict:
        return {
            "dest": str(self.dest),
            "linked": len(self.linked),
            "copied": len(self.copied),
            "delivered": len(self.linked) + len(self.copied),
            "skipped": len(self.skipped),
            "failed": self.failed,
            "copied_bytes": self.copied_bytes,
        }


def export_dir_for(folder: Path, root: Path | None = None, tag: str | None = None) -> Path:
    """Where a deliverable lands: <root>/<trip>, or <root>/<trip>/<tag> for videos."""
    base = (root or EXPORTS_ROOT) / folder.name
    if tag:
        return base / sanitize_tag(tag)
    return base


def _dest_for_shot(
    folder: Path, root: Path | None, shot: Path, tag: str | None
) -> Path:
    """Export directory for one deliverable — photos ignore tags, videos never do."""
    if is_video(shot):
        return export_dir_for(folder, root, tag or UNTAGGED_DIR)
    return export_dir_for(folder, root)


def _is_exported_clip(path: Path, folder: Path) -> bool:
    """True for files under <folder>/clips/ — cuts this app wrote, not sources."""
    try:
        rel = path.relative_to(folder)
    except ValueError:
        return False
    return rel.parts[:1] == (EXPORT_DIR_NAME,)


def export_shots(output_dir: Path, folder: Path) -> list[Path]:
    """Everything the trip delivers: every surviving photo, plus starred videos.

    Walks the folder rather than the decision record, because an undecided
    photo is still a photo the trip has — only an explicit delete takes one out
    of the deliverable set. Videos go the other way: they are in only when
    starred, and the decision record is the whole story.

    Filters on the filename before touching the filesystem, the way the video
    scan does: a trip is thousands of files, and stat'ing all of them to find
    the deliverables costs more than the export itself.
    """
    decisions = load_decisions(output_dir)
    folder = folder.resolve()
    shots: list[Path] = []
    for root, _dirs, files in os.walk(folder):
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            is_photo = ext in IMAGE_EXTENSIONS
            if not is_photo and ext not in VIDEO_EXTENSIONS:
                continue
            path = Path(root, name).resolve()
            if _is_exported_clip(path, folder):
                continue
            status = decisions.get(str(path))
            if is_photo:
                if status not in _EXCLUDED_PHOTO_STATUSES:
                    shots.append(path)
            elif status == FAVORITE:
                shots.append(path)
    return sorted(shots)


def missing_favorites(output_dir: Path) -> list[str]:
    """Starred paths the decision record still names but the disk no longer has.

    The export walks the disk, so a favourite whose file has gone missing would
    otherwise vanish silently. It is not fatal — the run delivers what is there
    — but it is worth reporting.
    """
    decisions = load_decisions(output_dir)
    return sorted(
        p for p, status in decisions.items() if status == FAVORITE and not Path(p).is_file()
    )


def _link_capable(folder: Path, dest: Path) -> bool:
    """Whether a hardlink from the project folder into `dest` can work.

    Hardlinks cannot cross filesystems, so this compares the device the sources
    are on against the device that will hold the export. `dest` itself is
    usually not created yet, so the nearest existing ancestor stands in for it.
    """
    probe = dest
    while not probe.exists():
        if probe.parent == probe:
            return False
        probe = probe.parent
    try:
        return folder.stat().st_dev == probe.stat().st_dev
    except OSError:
        return False


def plan_export(output_dir: Path, folder: Path, root: Path | None = None) -> dict:
    """What an export would deliver, without delivering anything.

    Feeds the confirmation dialog: `mode` says whether the run costs disk space
    at all — `"link"` hardlinks and costs nothing, `"copy"` writes the bytes, so
    the exports root's free space is worth seeing first.

    `files`/`bytes` describe the whole deliverable set; `pending`/`pending_bytes`
    describe what this run would actually deliver, with `delivered` counting what
    an earlier run already put there. The finish panel needs that split to tell
    "not exported yet" from "exported, then the page was reloaded".

    `destinations` breaks the same counts down by export folder — the trip root
    (photos), each video tag, and `untagged` all get their own row.
    """
    assignments = load_video_tags(output_dir)["videos"]
    dest = export_dir_for(folder, root)
    shots = export_shots(output_dir, folder)
    total = 0
    delivered = 0
    pending = 0
    pending_bytes = 0
    dest_stats: dict[str, dict] = {}

    def _dest_row(d: Path, tag_label: str | None) -> dict:
        key = str(d)
        if key not in dest_stats:
            dest_stats[key] = {
                "dest": key,
                "tag": tag_label,
                "files": 0,
                "bytes": 0,
                "delivered": 0,
                "pending": 0,
                "pending_bytes": 0,
            }
        return dest_stats[key]

    for shot in shots:
        tag = assignments.get(str(shot)) if is_video(shot) else None
        shot_dest = _dest_for_shot(folder, root, shot, tag)
        row = _dest_row(shot_dest, shot_dest.name if shot_dest != dest else None)
        try:
            size = shot.stat().st_size
        except OSError:
            size = 0
        total += size
        row["files"] += 1
        row["bytes"] += size
        try:
            already = shot_dest.is_dir() and _destination_for(shot, shot_dest) is None
        except OSError:
            already = False
        if already:
            delivered += 1
            row["delivered"] += 1
        else:
            pending += 1
            pending_bytes += size
            row["pending"] += 1
            row["pending_bytes"] += size
    destinations = sorted(dest_stats.values(), key=lambda r: (r["tag"] is not None, r["dest"]))
    return {
        "dest": str(dest),
        "mode": "link" if _link_capable(folder, dest) else "copy",
        "shots": len(shots),
        "files": len(shots),
        "bytes": total,
        "delivered": delivered,
        "pending": pending,
        "pending_bytes": pending_bytes,
        "missing": missing_favorites(output_dir),
        "free_bytes": _free_bytes(dest),
        "destinations": destinations,
    }


def _free_bytes(dest: Path) -> int | None:
    """Free space on the filesystem that would hold `dest`, or None if unknown.

    Walks up to the nearest existing ancestor, since `dest` itself is usually
    created by the export.
    """
    probe = dest
    while not probe.exists():
        if probe.parent == probe:
            return None
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        return None


def _destination_for(src: Path, dest_dir: Path) -> Path | None:
    """Where `src` should land, or None if an identical copy is already there.

    Two trips can hold the same camera filename, so a name already in use by a
    *different* file gets a numeric suffix rather than being overwritten. A name
    in use by a file of the same size is treated as already exported — this is
    what makes re-running an export cheap and idempotent, and it recognises a
    hardlink from an earlier run as readily as a copy.
    """
    size = src.stat().st_size
    stem, suffix = src.stem, src.suffix
    for i in range(MAX_NAME_ATTEMPTS):
        candidate = dest_dir / (f"{stem}{suffix}" if i == 0 else f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
        try:
            if candidate.stat().st_size == size:
                return None
        except OSError:
            continue
    raise OSError(f"No free filename for {src.name} after {MAX_NAME_ATTEMPTS} attempts")


def _place(src: Path, target: Path) -> bool:
    """Put `src` at `target`. True if it was hardlinked, False if copied.

    The link is tried first and its failure is the fallback's trigger, rather
    than deciding up front: a cross-device exports root, a filesystem with no
    hardlinks, and a source at its link limit all surface the same way, and all
    of them mean the same thing — write the bytes instead.
    """
    try:
        os.link(src, target)
        return True
    except OSError:
        partial = target.with_name(target.name + PARTIAL_SUFFIX)
        shutil.copy2(src, partial)
        partial.replace(target)
        return False


def export_trip(
    output_dir: Path, folder: Path, root: Path | None = None
) -> ExportReport:
    """Deliver every surviving photo and starred video into the trip's export dir.

    Tagged videos land in <trip>/<tag>/ and untagged ones in <trip>/untagged/;
    photos stay in <trip>/. One bad file does not sink the run: failures are
    collected per file and reported.
    """
    assignments = load_video_tags(output_dir)["videos"]
    dest = export_dir_for(folder, root)
    report = ExportReport(dest=dest)

    for shot in export_shots(output_dir, folder):
        tag = assignments.get(str(shot)) if is_video(shot) else None
        shot_dest = _dest_for_shot(folder, root, shot, tag)
        try:
            shot_dest.mkdir(parents=True, exist_ok=True)
            target = _destination_for(shot, shot_dest)
            if target is None:
                report.skipped.append(shot.name)
                continue
            if _place(shot, target):
                report.linked.append(target.name)
            else:
                report.copied.append(target.name)
                report.copied_bytes += target.stat().st_size
        except OSError as e:
            report.failed.append({"path": str(shot), "error": str(e)})

    for path in missing_favorites(output_dir):
        report.failed.append({"path": path, "error": "file not found"})
    return report

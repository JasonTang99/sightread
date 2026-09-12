"""Publish a trip's deliverables into an exports tree the editor opens.

Curation decides what is worth keeping; this puts it somewhere an editor will
look, grouped per trip:

    Trips/<trip>/xt5/                         <- the project folder, or the
    Trips/<trip>/                             <- whole trip, devices and all
    Trips/<trip>/_exports/DSCF4539.JPG
    Trips/<trip>/_exports/<tag>/DSCF4540.JPG
    Trips/<trip>/_exports/<tag or "untagged">/DSCF4601.MOV

With SIGHTREAD_EXPORTS_ROOT set, `Trips/<trip>/_exports` becomes
`<EXPORTS_ROOT>/<project folder name>` instead.

Three rules shape the rest of it.

**Every surviving photo, as a JPEG.** Stars pick what to cut with, not what to
keep, so the export delivers every photo the trip still has — anything not
marked for deletion — and the raw and `.xmp` sidecars stay behind in the import
folder. Videos are the exception: footage is delivered only when it was
starred, because a trip holds far more of it than an edit ever uses, and
starring is the only pass that has looked at it.

A HEIC is delivered as a JPEG too, because Resolve on Linux cannot open HEIF.
Many are JPEGs already under the wrong name — every one of the 183 `.HEIC`
files in Hoh River's Google Photos export is — and those are linked under a
`.jpg` name like any other photo. A real HEIF is re-encoded, the one case where
the export writes new pixels rather than linking the original.

A Live Photo's motion file follows its still's star: delivered beside the other
starred footage when the still is starred, left behind otherwise.

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

from PIL import Image

from clips import EXPORT_DIR_NAME
from media import (
    HEIF_EXTENSIONS,
    IMAGE_EXTENSIONS,
    TRIP_EXPORTS_DIR,
    VIDEO_EXTENSIONS,
    motion_names,
    register_heif,
    walk_media,
)
from utils import DELETED, FAVORITE, TO_DELETE, load_decisions
from video_tags import is_video, load_video_tags, sanitize_tag

register_heif()

# The exports root is a delivery location, not project state, so it lives
# outside the output dir. Unset, a trip delivers beside its camera folders:
# Trips/<trip>/xt5 is the project and Trips/<trip>/_exports gets the exports,
# which is how the drives are laid out since 2026-09-11. Set it to deliver into
# a separate tree instead, as <root>/<project folder name>/.
_env_root = os.environ.get("SIGHTREAD_EXPORTS_ROOT")
EXPORTS_ROOT: Path | None = Path(_env_root) if _env_root else None

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

# Re-encoded HEIFs are the only deliverables whose size is not the source's.
# Measured 1.77x over eight iPhone HEICs at quality 95; the free-space check
# budgets 2.5x so an estimate that runs short cannot fill the drive.
HEIF_JPEG_QUALITY = 95
HEIF_TO_JPEG_BUDGET = 2.5

_JPEG_MAGIC = b"\xff\xd8\xff"


@dataclass(frozen=True)
class Deliverable:
    """One file the export puts in place.

    `name` is the filename it gets, which differs from the source's only for a
    HEIC. `convert` is set for a real HEIF, which is re-encoded rather than
    linked. `tag_of` is the path whose tag routes it: its own, except for a
    Live Photo motion file, which goes where its still's tag sends it.
    """

    src: Path
    name: str
    convert: bool
    tag_of: str


def _is_jpeg(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(3) == _JPEG_MAGIC
    except OSError:
        return False


def _deliverable(src: Path, tag_of: Path | None = None) -> Deliverable:
    tag_key = str(tag_of or src)
    if src.suffix.lower() not in HEIF_EXTENSIONS:
        return Deliverable(src, src.name, False, tag_key)
    ext = ".jpg" if src.suffix.islower() else ".JPG"
    return Deliverable(src, src.stem + ext, not _is_jpeg(src), tag_key)


@dataclass
class ExportReport:
    """Outcome of one export run. Counts files, not shots."""

    dest: Path
    linked: list[str] = field(default_factory=list)
    copied: list[str] = field(default_factory=list)
    converted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    copied_bytes: int = 0
    converted_bytes: int = 0

    def as_dict(self) -> dict:
        return {
            "dest": str(self.dest),
            "linked": len(self.linked),
            "copied": len(self.copied),
            "converted": len(self.converted),
            "delivered": len(self.linked) + len(self.copied) + len(self.converted),
            "skipped": len(self.skipped),
            "failed": self.failed,
            "copied_bytes": self.copied_bytes,
            "converted_bytes": self.converted_bytes,
        }


def _holds_media(directory: Path) -> bool:
    """Whether any photo or video lives under `directory`. Stops at the first."""
    for dirpath, names in walk_media(directory):
        for name in names:
            ext = os.path.splitext(name)[1].lower()
            if ext in IMAGE_EXTENSIONS or ext in VIDEO_EXTENSIONS:
                return True
    return False


def trip_root(folder: Path) -> Path:
    """The trip folder whose `_exports` this project delivers into.

    A project is opened either on one camera folder — `Trips/<trip>/xt5`,
    whose deliverables belong to the trip beside it — or on the whole trip at
    once, which is how a trip's devices get reviewed together. The two are
    told apart by what is under the folder: a trip holds camera folders with
    media in them, a camera folder holds the media itself. An `_exports`
    already in place settles it without looking further.
    """
    if (folder / TRIP_EXPORTS_DIR).is_dir():
        return folder
    try:
        subdirs = [
            Path(e.path)
            for e in os.scandir(folder)
            if e.is_dir() and e.name != TRIP_EXPORTS_DIR and not e.name.startswith(".")
        ]
    except OSError:
        return folder.parent
    return folder if any(_holds_media(d) for d in subdirs) else folder.parent


def exports_anchor(folder: Path, root: Path | None = None) -> Path:
    """The directory that must already exist before anything is exported.

    It is the exports root when one is set, else the trip the project belongs
    to. Either one missing means an unmounted drive.
    """
    root = root or EXPORTS_ROOT
    return root if root else trip_root(folder)


def export_dir_for(folder: Path, root: Path | None = None, tag: str | None = None) -> Path:
    """Where a deliverable lands: <trip>/_exports (or <root>/<folder name>), plus /<tag>."""
    root = root or EXPORTS_ROOT
    base = root / folder.name if root else trip_root(folder) / TRIP_EXPORTS_DIR
    if tag:
        return base / sanitize_tag(tag)
    return base


def _dest_for_shot(
    folder: Path, root: Path | None, shot: Path, tag: str | None
) -> Path:
    """Export directory for one deliverable.

    Videos always get a subfolder (tag or untagged). Photos only do when
    tagged, so the untagged stills stay in the trip root.
    """
    if is_video(shot):
        return export_dir_for(folder, root, tag or UNTAGGED_DIR)
    return export_dir_for(folder, root, tag)


def _is_exported_clip(path: Path, folder: Path) -> bool:
    """True for files under <folder>/clips/ — cuts this app wrote, not sources."""
    try:
        rel = path.relative_to(folder)
    except ValueError:
        return False
    return rel.parts[:1] == (EXPORT_DIR_NAME,)


def deliverables(output_dir: Path, folder: Path) -> list[Deliverable]:
    """Everything the trip delivers: every surviving photo, plus starred videos.

    Walks the folder rather than the decision record, because an undecided
    photo is still a photo the trip has — only an explicit delete takes one out
    of the deliverable set. Videos go the other way: they are in only when
    starred, and the decision record is the whole story. A Live Photo's motion
    file is never judged on its own, so it rides on its still's star instead.

    Filters on the filename before touching the filesystem, the way the video
    scan does: a trip is thousands of files, and stat'ing all of them to find
    the deliverables costs more than the export itself.
    """
    decisions = load_decisions(output_dir)
    folder = folder.resolve()
    out: list[Deliverable] = []
    for root, files in walk_media(folder):
        motion = motion_names(root, files)
        motion_by_stem = {os.path.splitext(n)[0]: n for n in motion}
        for name in files:
            ext = os.path.splitext(name)[1].lower()
            is_photo = ext in IMAGE_EXTENSIONS
            if not is_photo and ext not in VIDEO_EXTENSIONS:
                continue
            if name in motion:
                continue  # delivered with its still, below, or not at all
            path = Path(root, name).resolve()
            if _is_exported_clip(path, folder):
                continue
            status = decisions.get(str(path))
            if is_photo:
                if status in _EXCLUDED_PHOTO_STATUSES:
                    continue
                out.append(_deliverable(path))
                partner = motion_by_stem.get(os.path.splitext(name)[0])
                if status == FAVORITE and partner is not None:
                    out.append(_deliverable(Path(root, partner).resolve(), tag_of=path))
            elif status == FAVORITE:
                out.append(_deliverable(path))
    return sorted(out, key=lambda d: d.src)


def export_shots(output_dir: Path, folder: Path) -> list[Path]:
    """The source files `deliverables` would export."""
    return [d.src for d in deliverables(output_dir, folder)]


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
    items = deliverables(output_dir, folder)
    total = 0
    delivered = 0
    pending = 0
    pending_bytes = 0
    convert = 0
    convert_bytes = 0
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

    for item in items:
        tag = assignments.get(item.tag_of)
        shot_dest = _dest_for_shot(folder, root, item.src, tag)
        row = _dest_row(shot_dest, shot_dest.name if shot_dest != dest else None)
        try:
            size = item.src.stat().st_size
        except OSError:
            size = 0
        total += size
        row["files"] += 1
        row["bytes"] += size
        try:
            already = shot_dest.is_dir() and _destination_for(item, shot_dest) is None
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
            if item.convert:
                convert += 1
                convert_bytes += int(size * HEIF_TO_JPEG_BUDGET)
    destinations = sorted(dest_stats.values(), key=lambda r: (r["tag"] is not None, r["dest"]))
    return {
        "dest": str(dest),
        "mode": "link" if _link_capable(folder, dest) else "copy",
        "shots": len(items),
        "files": len(items),
        "bytes": total,
        "delivered": delivered,
        "pending": pending,
        "pending_bytes": pending_bytes,
        # Pending HEIFs that will be re-encoded, and the space budgeted for
        # them. Written in either mode: a new JPEG is never a hardlink.
        "convert": convert,
        "convert_bytes": convert_bytes,
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


def _same_delivery(item: Deliverable, src_st: os.stat_result, candidate: Path) -> bool:
    """Whether `candidate` is this deliverable, put there by an earlier run.

    A link or copy has the source's size. A re-encoded HEIF cannot, so the
    conversion stamps the source's mtime on its output and that is compared
    instead — to the second, since a copy onto a coarser filesystem rounds it.
    """
    st = candidate.stat()
    if item.convert:
        return abs(st.st_mtime - src_st.st_mtime) < 1.0
    return st.st_size == src_st.st_size


def _destination_for(item: Deliverable | Path, dest_dir: Path) -> Path | None:
    """Where a deliverable should land, or None if it is already there.

    Two trips can hold the same camera filename, so a name already in use by a
    *different* file gets a numeric suffix rather than being overwritten. A name
    in use by the same file is treated as already exported — this is what makes
    re-running an export cheap and idempotent, and it recognises a hardlink
    from an earlier run as readily as a copy.
    """
    if isinstance(item, Path):
        item = _deliverable(item)
    src_st = item.src.stat()
    stem, suffix = os.path.splitext(item.name)
    for i in range(MAX_NAME_ATTEMPTS):
        candidate = dest_dir / (f"{stem}{suffix}" if i == 0 else f"{stem}_{i}{suffix}")
        if not candidate.exists():
            return candidate
        try:
            if _same_delivery(item, src_st, candidate):
                return None
        except OSError:
            continue
    raise OSError(f"No free filename for {item.name} after {MAX_NAME_ATTEMPTS} attempts")


def _convert(src: Path, target: Path) -> None:
    """Re-encode a HEIF as a JPEG at `target`, keeping its EXIF and colour profile.

    pillow-heif applies the HEIF's rotation while decoding and resets the EXIF
    orientation to 1, so the pixels are written upright and nothing downstream
    rotates them twice. Written to a partial name first, like a copy, and
    stamped with the source's mtime so a re-run recognises it.
    """
    partial = target.with_name(target.name + PARTIAL_SUFFIX)
    try:
        with Image.open(src) as img:
            extra = {"exif": img.getexif().tobytes()}
            if img.info.get("icc_profile"):
                extra["icc_profile"] = img.info["icc_profile"]
            img.convert("RGB").save(partial, format="JPEG", quality=HEIF_JPEG_QUALITY, **extra)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    st = src.stat()
    os.utime(partial, ns=(st.st_atime_ns, st.st_mtime_ns))
    partial.replace(target)


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

    Tagged shots land in <trip>/<tag>/; untagged videos in <trip>/untagged/;
    untagged photos stay in <trip>/. One bad file does not sink the run:
    failures are collected per file and reported.
    """
    assignments = load_video_tags(output_dir)["videos"]
    dest = export_dir_for(folder, root)
    report = ExportReport(dest=dest)

    for item in deliverables(output_dir, folder):
        shot = item.src
        tag = assignments.get(item.tag_of)
        shot_dest = _dest_for_shot(folder, root, shot, tag)
        try:
            shot_dest.mkdir(parents=True, exist_ok=True)
            target = _destination_for(item, shot_dest)
            if target is None:
                report.skipped.append(item.name)
                continue
            if item.convert:
                _convert(shot, target)
                report.converted.append(target.name)
                report.converted_bytes += target.stat().st_size
            elif _place(shot, target):
                report.linked.append(target.name)
            else:
                report.copied.append(target.name)
                report.copied_bytes += target.stat().st_size
        except (OSError, ValueError, RuntimeError) as e:
            # ValueError / RuntimeError: how Pillow and libheif report a HEIF
            # they cannot decode; OSError covers everything on the disk side.
            report.failed.append({"path": str(shot), "error": str(e)})

    for path in missing_favorites(output_dir):
        report.failed.append({"path": path, "error": "file not found"})
    return report

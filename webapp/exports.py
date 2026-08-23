"""Copy a trip's favourites out of the project folder into an exports tree.

This is the delivery step: curation decides what is worth keeping, and this puts
those files somewhere an editor will look, grouped per trip:

    <EXPORTS_ROOT>/<project folder name>/DSCF4539.JPG

Two rules shape the rest of it.

**Originals only.** Never the derived caches — `thumb_cache` holds resized JPEGs
and `video_cache` holds a 1440p bitrate-capped transcode built for scrubbing, not
for editing. This follows the rule clip export already states.

**A favourite is a shot, not a file.** The same grouping deletion uses: the JPEG
the pipeline ranked, the raw beside it, and their `.xmp` sidecars all travel
together, because the raw is the file you actually edit and a JPEG exported
without it is half a deliverable. Videos have no raw, so a favourited video
exports as the single original file.

Nothing here is destructive: it only ever writes into the exports tree, and it
never overwrites an existing file.
"""
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from utils import FAVORITE, load_decisions, paths_with_status, sidecars_of

# The exports root is a delivery location, not project state, so it lives
# outside the output dir and is configurable for anyone whose drives differ.
EXPORTS_ROOT = Path(os.environ.get("SIGHTREAD_EXPORTS_ROOT", "/mnt/h0/Editing/exports"))

# A partial copy that kept the final name would be indistinguishable from a
# finished one on the next run, since the skip check compares sizes. Copies land
# on this suffix and are renamed into place only once complete.
PARTIAL_SUFFIX = ".sightread-part"

# Collision suffixes to try before giving up on a name.
MAX_NAME_ATTEMPTS = 100


@dataclass
class ExportReport:
    """Outcome of one export run. Counts files, not shots."""

    dest: Path
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[dict] = field(default_factory=list)
    copied_bytes: int = 0

    def as_dict(self) -> dict:
        return {
            "dest": str(self.dest),
            "copied": len(self.copied),
            "skipped": len(self.skipped),
            "failed": self.failed,
            "copied_bytes": self.copied_bytes,
        }


def export_dir_for(folder: Path, root: Path | None = None) -> Path:
    """Where `folder`'s favourites go: <root>/<folder basename>."""
    return (root or EXPORTS_ROOT) / folder.name


def favorite_shots(output_dir: Path) -> list[Path]:
    """Every favourited path that is still on disk, as absolute paths.

    A favourite whose file has gone missing is dropped rather than raised on:
    the export should deliver what it can and report the rest.
    """
    paths = paths_with_status(load_decisions(output_dir), FAVORITE)
    return [Path(p) for p in sorted(paths)]


def files_for(shot: Path) -> list[Path]:
    """The shot's own files: the image plus its raw and `.xmp` sidecars."""
    return [shot, *sidecars_of(shot)]


def plan_export(output_dir: Path, folder: Path, root: Path | None = None) -> dict:
    """What an export would copy, without copying anything.

    Feeds the confirmation dialog: the exports root may sit on the same drive
    the deletes just freed, so the size is worth seeing before agreeing to it.
    """
    dest = export_dir_for(folder, root)
    shots = favorite_shots(output_dir)
    files: list[Path] = []
    missing: list[str] = []
    for shot in shots:
        if not shot.is_file():
            missing.append(str(shot))
            continue
        files.extend(files_for(shot))
    total = 0
    for f in files:
        try:
            total += f.stat().st_size
        except OSError:
            pass
    return {
        "dest": str(dest),
        "shots": len(shots) - len(missing),
        "files": len(files),
        "bytes": total,
        "missing": missing,
        "free_bytes": _free_bytes(dest),
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
    what makes re-running an export cheap and idempotent.
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


def export_favorites(
    output_dir: Path, folder: Path, root: Path | None = None
) -> ExportReport:
    """Copy every favourited shot into the trip's export directory.

    One bad file does not sink the run: failures are collected per file and
    reported, because a permission error on a single raw should not cost the
    other fifteen shots.
    """
    dest = export_dir_for(folder, root)
    report = ExportReport(dest=dest)
    dest.mkdir(parents=True, exist_ok=True)

    for shot in favorite_shots(output_dir):
        if not shot.is_file():
            report.failed.append({"path": str(shot), "error": "file not found"})
            continue
        for src in files_for(shot):
            try:
                target = _destination_for(src, dest)
                if target is None:
                    report.skipped.append(src.name)
                    continue
                partial = target.with_name(target.name + PARTIAL_SUFFIX)
                shutil.copy2(src, partial)
                partial.replace(target)
                report.copied.append(target.name)
                report.copied_bytes += target.stat().st_size
            except OSError as e:
                report.failed.append({"path": str(src), "error": str(e)})
    return report

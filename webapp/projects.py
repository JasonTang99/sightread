"""Project registry: context, recents, staleness detection."""
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

_xdg_config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
_xdg_data = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))

CONFIG_DIR = _xdg_config / "sightread"
DATA_DIR = _xdg_data / "sightread" / "projects"
RECENTS_FILE = CONFIG_DIR / "recents.json"

ProjectStatus = Literal["ready", "stale", "never_run"]


@dataclass
class ProjectContext:
    folder: Path
    output_dir: Path


def project_output_dir(folder: Path) -> Path:
    key = hashlib.md5(str(folder.resolve()).encode()).hexdigest()
    return DATA_DIR / key


def image_files_in(folder: Path) -> set[str]:
    return {
        str(p.resolve())
        for p in folder.rglob("*")
        if p.suffix.lower() in IMAGE_EXTENSIONS
    }


def project_status(folder: Path, output_dir: Path) -> ProjectStatus:
    sidecar = output_dir / "embeddings_dinov3_mpcls_tta.paths.json"
    if not sidecar.exists():
        return "never_run"
    try:
        processed = set(json.loads(sidecar.read_text()))
    except Exception:
        return "never_run"
    current = image_files_in(folder)
    return "ready" if processed == current else "stale"


def load_recents() -> list[dict]:
    if not RECENTS_FILE.exists():
        return []
    try:
        return json.loads(RECENTS_FILE.read_text())
    except Exception:
        return []


def save_recents(entries: list[dict]) -> None:
    RECENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    RECENTS_FILE.write_text(json.dumps(entries, indent=2))


def upsert_recent(folder: Path, output_dir: Path, pipeline_ran: bool = False) -> None:
    entries = load_recents()
    folder_str = str(folder.resolve())
    now = datetime.now(timezone.utc).isoformat()
    match = next((e for e in entries if e["folder"] == folder_str), None)
    if match:
        match["last_opened"] = now
        if pipeline_ran:
            match["last_pipeline_run"] = now
            match["image_count"] = len(image_files_in(folder))
    else:
        entries.insert(0, {
            "folder": folder_str,
            "output_dir": str(output_dir),
            "last_opened": now,
            "last_pipeline_run": now if pipeline_ran else None,
            "image_count": len(image_files_in(folder)),
        })
    entries.sort(key=lambda e: e.get("last_opened", ""), reverse=True)
    save_recents(entries[:20])

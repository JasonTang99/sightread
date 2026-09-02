"""Named tags for favourited videos — persistence + export routing.

Persistence lives in <output_dir>/video_tags.json:

    {
      "schema_version": 1,
      "tags": ["b-roll", "timelapse"],
      "videos": {"/abs/path.MOV": "b-roll"}
    }

Tags are project-scoped. Only videos use them at export time: photos always land
in the trip root; a video goes to <trip>/<tag>/, or <trip>/untagged/ when it has
no tag.
"""
import json
import re
from pathlib import Path

VIDEO_TAGS_FILENAME = "video_tags.json"
SCHEMA_VERSION = 1

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v", ".m2ts", ".mts", ".webm"}

# A project that has never been tagged starts with these, so the 1/2/3 keys in
# the Videos tab mean the same thing on every trip. They are a starting point,
# not a fixed set: tags can be added, and a project whose video_tags.json exists
# is taken exactly as written, including one whose list has been emptied.
DEFAULT_TAGS = ["vibes", "people", "action"]


def is_video(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def sanitize_tag(name: str) -> str:
    """Turn a user label into a safe single path segment."""
    name = name.strip()
    if not name:
        raise ValueError("empty tag")
    name = re.sub(r"[/\\]+", "-", name)
    name = re.sub(r"[\x00-\x1f]", "", name).strip()
    if not name or name in (".", ".."):
        raise ValueError("invalid tag")
    return name


def _normalize_tags(tags: list) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags:
        if not isinstance(raw, str):
            continue
        try:
            tag = sanitize_tag(raw)
        except ValueError:
            continue
        if tag not in seen:
            seen.add(tag)
            out.append(tag)
    return out


def _empty() -> dict:
    return {"schema_version": SCHEMA_VERSION, "tags": list(DEFAULT_TAGS), "videos": {}}


def load_video_tags(output_dir: Path) -> dict:
    """Full video_tags.json payload, or the empty default."""
    path = output_dir / VIDEO_TAGS_FILENAME
    try:
        data = json.loads(path.read_text())
        tags = _normalize_tags(data.get("tags", []))
        videos = data.get("videos", {})
        if not isinstance(videos, dict):
            videos = {}
        cleaned = {}
        allowed = set(tags)
        for p, t in videos.items():
            if isinstance(p, str) and isinstance(t, str):
                try:
                    tag = sanitize_tag(t)
                except ValueError:
                    continue
                if tag in allowed:
                    cleaned[p] = tag
        return {"schema_version": SCHEMA_VERSION, "tags": tags, "videos": cleaned}
    except Exception:
        return _empty()


def save_video_tags(output_dir: Path, data: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / VIDEO_TAGS_FILENAME
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    tmp.replace(target)


def tag_for_video(output_dir: Path, video_path: str) -> str | None:
    return load_video_tags(output_dir)["videos"].get(video_path)


def update_video_tags(
    output_dir: Path,
    *,
    tags: list[str] | None = None,
    assign: dict[str, str | None] | None = None,
) -> dict:
    """Merge tag-list and/or path assignments; return the stored payload."""
    data = load_video_tags(output_dir)
    if tags is not None:
        data["tags"] = _normalize_tags(tags)
        allowed = set(data["tags"])
        data["videos"] = {p: t for p, t in data["videos"].items() if t in allowed}
    if assign:
        for path, tag in assign.items():
            if not isinstance(path, str):
                continue
            if tag is None or tag == "":
                data["videos"].pop(path, None)
                continue
            normalized = sanitize_tag(tag)
            if normalized not in data["tags"]:
                data["tags"].append(normalized)
            data["videos"][path] = normalized
    save_video_tags(output_dir, data)
    return data

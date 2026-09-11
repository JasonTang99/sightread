"""Tests for video tags — persistence, API, and export routing."""

import pytest
from fastapi.testclient import TestClient

import exports
import server
from exports import export_trip, plan_export
from projects import ProjectContext
from utils import FAVORITE, save_decisions
from video_tags import DEFAULT_TAGS, load_video_tags, sanitize_tag, update_video_tags


@pytest.fixture()
def project(tmp_path, monkeypatch):
    folder = tmp_path / "trips" / "2026_01_Japan"
    folder.mkdir(parents=True)
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    root = tmp_path / "exports"
    root.mkdir()
    return folder, output_dir, root


@pytest.fixture()
def api(project, monkeypatch):
    folder, output_dir, root = project
    monkeypatch.setattr(exports, "EXPORTS_ROOT", root)
    monkeypatch.setattr(server, "EXPORTS_ROOT", root)
    monkeypatch.setattr(
        server, "_active", ProjectContext(folder=folder, output_dir=output_dir)
    )
    return TestClient(server.app, base_url="http://localhost"), folder, output_dir, root


def _video(folder, name=b"video"):
    mov = folder / "clip.MOV"
    mov.write_bytes(name)
    return mov


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_sanitize_tag_rejects_empty_and_separators():
    assert sanitize_tag("b-roll") == "b-roll"
    assert sanitize_tag("  timelapse  ") == "timelapse"
    assert sanitize_tag("foo/bar") == "foo-bar"
    with pytest.raises(ValueError):
        sanitize_tag("")
    with pytest.raises(ValueError):
        sanitize_tag("..")


def test_assign_auto_adds_tag(project):
    _, output_dir, _ = project
    mov = _video(project[0])
    data = update_video_tags(output_dir, assign={str(mov): "b-roll"})
    assert data["tags"] == [*DEFAULT_TAGS, "b-roll"]
    assert data["videos"][str(mov)] == "b-roll"


def test_removing_tag_from_list_clears_assignments(project):
    _, output_dir, _ = project
    mov = _video(project[0])
    update_video_tags(output_dir, tags=["a", "b"], assign={str(mov): "a"})
    data = update_video_tags(output_dir, tags=["b"])
    assert "a" not in data["tags"]
    assert str(mov) not in data["videos"]


def test_clear_assignment(project):
    _, output_dir, _ = project
    mov = _video(project[0])
    update_video_tags(output_dir, assign={str(mov): "b-roll"})
    data = update_video_tags(output_dir, assign={str(mov): None})
    assert str(mov) not in data["videos"]


# ---------------------------------------------------------------------------
# Export routing
# ---------------------------------------------------------------------------


def test_tagged_video_exports_to_tag_subfolder(project):
    folder, output_dir, root = project
    mov = folder / "clip.MOV"
    mov.write_bytes(b"mov-a")
    other = folder / "other.MOV"
    other.write_bytes(b"mov-b")
    save_decisions(output_dir, {str(mov): FAVORITE, str(other): FAVORITE})
    update_video_tags(output_dir, tags=["b-roll"], assign={str(mov): "b-roll"})

    export_trip(output_dir, folder, root)

    assert (root / "2026_01_Japan" / "untagged" / "other.MOV").is_file()
    assert (root / "2026_01_Japan" / "b-roll" / "clip.MOV").is_file()
    assert not (root / "2026_01_Japan" / "clip.MOV").exists()
    assert not (root / "2026_01_Japan" / "other.MOV").exists()


def test_plan_export_lists_destinations(project):
    folder, output_dir, root = project
    mov = _video(folder)
    save_decisions(output_dir, {str(mov): FAVORITE})
    update_video_tags(output_dir, assign={str(mov): "b-roll"})

    plan = plan_export(output_dir, folder, root)

    assert len(plan["destinations"]) == 1
    assert plan["destinations"][0]["tag"] == "b-roll"
    assert plan["destinations"][0]["pending"] == 1


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_get_video_tags(api):
    client, folder, output_dir, _ = api
    mov = _video(folder)
    update_video_tags(output_dir, assign={str(mov): "b-roll"})

    data = client.get("/api/video-tags").json()

    assert data["tags"] == [*DEFAULT_TAGS, "b-roll"]
    assert data["assignments"][str(mov)] == "b-roll"


def test_put_video_tags_accepts_a_photo(api):
    client, folder, output_dir, _ = api
    jpg = folder / "photo.JPG"
    jpg.write_bytes(b"j")

    res = client.put("/api/video-tags", json={"assign": {str(jpg): "b-roll"}})

    assert res.status_code == 200
    assert res.json()["assignments"][str(jpg)] == "b-roll"


def test_put_video_tags_rejects_non_media(api):
    client, folder, output_dir, _ = api
    txt = folder / "notes.txt"
    txt.write_bytes(b"x")

    res = client.put("/api/video-tags", json={"assign": {str(txt): "b-roll"}})

    assert res.status_code == 400


def test_videos_endpoint_includes_tags(api):
    client, folder, output_dir, _ = api
    mov = _video(folder)
    update_video_tags(output_dir, assign={str(mov): "b-roll"})

    body = client.get("/api/videos").json()

    assert body["video_tags"]["tags"] == [*DEFAULT_TAGS, "b-roll"]
    assert body["video_tags"]["assignments"][str(mov)] == "b-roll"


def test_corrupt_tags_file_degrades(api, project):
    client, _, output_dir, _ = api
    (output_dir / "video_tags.json").write_text("{bad")

    data = client.get("/api/video-tags").json()

    assert data == {"tags": DEFAULT_TAGS, "assignments": {}}


def test_load_video_tags_missing_file(project):
    _, output_dir, _ = project
    assert load_video_tags(output_dir) == {
        "schema_version": 1,
        "tags": DEFAULT_TAGS,
        "videos": {},
    }


def test_default_tags_are_the_review_vocabulary(project):
    """1/2/3 in the Videos tab mean the same thing on a project never tagged."""
    _, output_dir, _ = project
    assert load_video_tags(output_dir)["tags"] == ["vibes", "people", "action"]


def test_an_emptied_tag_list_is_not_reseeded(project):
    """Defaults seed a fresh project; they do not fight a deliberate clear."""
    _, output_dir, _ = project
    update_video_tags(output_dir, tags=[])
    assert load_video_tags(output_dir)["tags"] == []


def test_tagged_photo_exports_to_tag_subfolder(project):
    """A tag on a still is an export folder, same as on a clip."""
    folder, output_dir, root = project
    tagged = folder / "tagged.JPG"
    tagged.write_bytes(b"j-tag")
    plain = folder / "plain.JPG"
    plain.write_bytes(b"j-plain")
    save_decisions(output_dir, {str(tagged): FAVORITE})
    update_video_tags(output_dir, tags=["vibes"], assign={str(tagged): "vibes"})

    export_trip(output_dir, folder, root)

    assert (root / "2026_01_Japan" / "vibes" / "tagged.JPG").is_file()
    assert (root / "2026_01_Japan" / "plain.JPG").is_file()
    assert not (root / "2026_01_Japan" / "tagged.JPG").exists()


def test_untagged_video_exports_to_its_own_folder(project):
    """An untagged clip is unsorted, not a still: it never lands beside photos."""
    folder, output_dir, root = project
    mov = _video(folder)
    jpg = folder / "photo.JPG"
    jpg.write_bytes(b"j")
    save_decisions(output_dir, {str(mov): FAVORITE, str(jpg): FAVORITE})

    export_trip(output_dir, folder, root)

    assert (root / "2026_01_Japan" / "untagged" / "clip.MOV").is_file()
    assert (root / "2026_01_Japan" / "photo.JPG").is_file()


def test_plan_export_rows_untagged_separately(project):
    folder, output_dir, root = project
    tagged = _video(folder)
    untagged = folder / "other.MOV"
    untagged.write_bytes(b"mov-b")
    save_decisions(output_dir, {str(tagged): FAVORITE, str(untagged): FAVORITE})
    update_video_tags(output_dir, assign={str(tagged): "b-roll"})

    plan = plan_export(output_dir, folder, root)

    rows = {r["tag"]: r for r in plan["destinations"]}
    assert set(rows) == {"b-roll", "untagged"}
    assert rows["untagged"]["pending"] == 1

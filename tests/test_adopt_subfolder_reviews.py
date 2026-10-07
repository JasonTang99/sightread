"""A trip adopts the reviews already done on its device folders.

A project is its folder path, so `<trip>/xt5` and `<trip>` are two projects
with two output dirs. Reviewing the camera folder first — which is what
happened to Japan, Hoh River and Portugal before trips could be opened whole —
left the trip project with no opinion about a single one of those photos, and
it would walk the user back through every keeper it had already been told to
keep.

It runs the other way too: `<trip>/xt5` opened after its trip pulls in what
was decided there about its own photos. Where the two disagree, the most
recent decision wins.
"""

import json

import projects
import pytest
from clips import load_user_clips, save_user_clips
from utils import (
    DELETED,
    FAVORITE,
    KEPT,
    TO_DELETE,
    load_decision_times,
    load_decisions,
    save_decisions,
)
from video_tags import load_video_tags, save_video_tags


@pytest.fixture()
def trip(tmp_path, monkeypatch):
    """A trip with an `xt5/` folder whose own project holds a finished review."""
    monkeypatch.setattr(projects, "DATA_DIR", tmp_path / "data")
    folder = tmp_path / "Trips" / "2026_01_Japan"
    (folder / "xt5").mkdir(parents=True)
    (folder / "iphone").mkdir()
    return folder


def _child_out(folder):
    out = projects.project_output_dir(folder)
    out.mkdir(parents=True, exist_ok=True)
    return out


def test_the_camera_folders_decisions_come_across(trip):
    child = _child_out(trip / "xt5")
    save_decisions(child, {
        str(trip / "xt5" / "DSCF1.JPG"): KEPT,
        str(trip / "xt5" / "DSCF2.JPG"): FAVORITE,
        str(trip / "xt5" / "DSCF3.JPG"): DELETED,
    })
    out = _child_out(trip)

    adopted = projects.adopt_subfolder_reviews(trip, out)

    assert adopted["photos"] == 3
    assert load_decisions(out) == {
        str(trip / "xt5" / "DSCF1.JPG"): KEPT,
        str(trip / "xt5" / "DSCF2.JPG"): FAVORITE,
        str(trip / "xt5" / "DSCF3.JPG"): DELETED,
    }


def test_applied_deletes_come_across_too(trip):
    """They name files that are gone, which is what keeps them from being
    proposed again — the trip would otherwise hold no record of them."""
    child = _child_out(trip / "xt5")
    save_decisions(child, {str(trip / "xt5" / "gone.JPG"): DELETED})
    out = _child_out(trip)

    projects.adopt_subfolder_reviews(trip, out)

    assert load_decisions(out)[str(trip / "xt5" / "gone.JPG")] == DELETED


def test_the_trips_own_decision_wins(trip):
    shot = str(trip / "xt5" / "DSCF1.JPG")
    child = _child_out(trip / "xt5")
    save_decisions(child, {shot: TO_DELETE})
    out = _child_out(trip)
    save_decisions(out, {shot: FAVORITE})

    adopted = projects.adopt_subfolder_reviews(trip, out)

    assert adopted["photos"] == 0
    assert load_decisions(out)[shot] == FAVORITE


def test_running_it_again_changes_nothing(trip):
    """It runs on every open, so a second pass must not undo later review."""
    shot = str(trip / "xt5" / "DSCF1.JPG")
    child = _child_out(trip / "xt5")
    save_decisions(child, {shot: KEPT})
    out = _child_out(trip)
    projects.adopt_subfolder_reviews(trip, out)

    # The user changes their mind in the trip project.
    save_decisions(out, {shot: TO_DELETE})
    adopted = projects.adopt_subfolder_reviews(trip, out)

    assert adopted["photos"] == 0
    assert load_decisions(out)[shot] == TO_DELETE


def test_several_camera_folders_are_all_adopted(trip):
    save_decisions(_child_out(trip / "xt5"), {str(trip / "xt5" / "a.JPG"): KEPT})
    save_decisions(_child_out(trip / "iphone"), {str(trip / "iphone" / "b.JPG"): FAVORITE})
    out = _child_out(trip)

    adopted = projects.adopt_subfolder_reviews(trip, out)

    assert adopted["folders"] == 2
    assert len(load_decisions(out)) == 2


def test_a_nested_import_folder_is_reached(trip):
    """Older imports nested a date under the camera: `canon/02-06/`."""
    nested = trip / "canon" / "02-06"
    nested.mkdir(parents=True)
    save_decisions(_child_out(nested), {str(nested / "IMG_1.JPG"): KEPT})
    out = _child_out(trip)

    assert projects.adopt_subfolder_reviews(trip, out)["photos"] == 1


def test_the_export_tree_is_not_a_camera_folder(trip):
    """`_exports/` holds delivered copies; a decision there is not review."""
    exports = trip / "_exports"
    exports.mkdir()
    save_decisions(_child_out(exports), {str(exports / "x.JPG"): KEPT})
    out = _child_out(trip)

    assert projects.adopt_subfolder_reviews(trip, out)["photos"] == 0


def test_a_decision_about_another_trip_is_left_alone(trip, tmp_path):
    """Nothing should let one trip's project write another trip's photos in."""
    other = tmp_path / "Trips" / "2026_08_Portugal" / "xt5" / "DSCF9.JPG"
    save_decisions(_child_out(trip / "xt5"), {str(other): KEPT})
    out = _child_out(trip)

    assert projects.adopt_subfolder_reviews(trip, out)["photos"] == 0


def test_video_tags_come_across_with_their_tag_name(trip):
    """An assignment naming a tag the trip does not have would be dropped on
    load, so the tag list has to travel with it."""
    child = _child_out(trip / "xt5")
    clip = str(trip / "xt5" / "DSCF4360.MOV")
    save_video_tags(child, {"schema_version": 1, "tags": ["vibes", "b-roll"],
                            "videos": {clip: "b-roll"}})
    out = _child_out(trip)

    adopted = projects.adopt_subfolder_reviews(trip, out)

    assert adopted["video_tags"] == 1
    tags = load_video_tags(out)
    assert tags["videos"][clip] == "b-roll"
    assert "b-roll" in tags["tags"]


def test_a_tag_set_on_the_trip_wins(trip):
    clip = str(trip / "xt5" / "DSCF4360.MOV")
    save_video_tags(_child_out(trip / "xt5"),
                    {"schema_version": 1, "tags": ["vibes"], "videos": {clip: "vibes"}})
    out = _child_out(trip)
    save_video_tags(out, {"schema_version": 1, "tags": ["vibes", "people"],
                          "videos": {clip: "people"}})

    projects.adopt_subfolder_reviews(trip, out)

    assert load_video_tags(out)["videos"][clip] == "people"


def test_user_clips_come_across(trip):
    clip = str(trip / "xt5" / "DSCF4470.MOV")
    save_user_clips(_child_out(trip / "xt5"), clip, [{"start": 0.7, "end": 4.0}])
    out = _child_out(trip)

    adopted = projects.adopt_subfolder_reviews(trip, out)

    assert adopted["clips"] == 1
    assert load_user_clips(out)[clip]["clips"] == [{"start": 0.7, "end": 4.0}]


def test_clips_edited_on_the_trip_are_not_overwritten(trip):
    clip = str(trip / "xt5" / "DSCF4470.MOV")
    save_user_clips(_child_out(trip / "xt5"), clip, [{"start": 0.7, "end": 4.0}])
    out = _child_out(trip)
    save_user_clips(out, clip, [{"start": 1.0, "end": 2.0}])

    projects.adopt_subfolder_reviews(trip, out)

    assert load_user_clips(out)[clip]["clips"] == [{"start": 1.0, "end": 2.0}]


def test_a_trip_with_no_reviewed_camera_folder_is_untouched(trip):
    out = _child_out(trip)

    adopted = projects.adopt_subfolder_reviews(trip, out)

    assert adopted == {"photos": 0, "video_tags": 0, "clips": 0, "folders": 0}
    assert not (out / "decisions.json").exists()


def test_opening_a_trip_adopts_without_a_pipeline_run(trip, monkeypatch):
    """Adoption happens on open, so it does not wait for a re-run."""
    import server

    save_decisions(_child_out(trip / "xt5"), {str(trip / "xt5" / "a.JPG"): KEPT})
    monkeypatch.setattr(server, "project_output_dir", projects.project_output_dir)
    monkeypatch.setattr(server, "upsert_recent", lambda *a, **k: None)

    server.open_project(server.FolderRequest(folder=str(trip)))

    assert load_decisions(projects.project_output_dir(trip)) == {
        str(trip / "xt5" / "a.JPG"): KEPT
    }


# ---------------------------------------------------------------------------
# The other direction: a device folder opened after its trip
# ---------------------------------------------------------------------------
def test_a_camera_folder_adopts_the_trips_decisions_about_it(trip):
    """Reviewing the trip, then opening `xt5/` alone, must not offer the
    trip's verdicts again — and must not take in the phone's."""
    camera = trip / "xt5"
    save_decisions(_child_out(trip), {
        str(camera / "DSCF1.JPG"): TO_DELETE,
        str(camera / "DSCF2.JPG"): FAVORITE,
        str(trip / "iphone" / "IMG_1.HEIC"): KEPT,
    })
    out = _child_out(camera)

    adopted = projects.adopt_subfolder_reviews(camera, out)

    assert adopted["photos"] == 2
    assert load_decisions(out) == {
        str(camera / "DSCF1.JPG"): TO_DELETE,
        str(camera / "DSCF2.JPG"): FAVORITE,
    }


def test_a_newer_trip_decision_replaces_an_older_camera_one(trip):
    shot = str(trip / "xt5" / "DSCF1.JPG")
    out = _child_out(trip / "xt5")
    save_decisions(out, {shot: KEPT}, at={shot: 100.0})
    save_decisions(_child_out(trip), {shot: TO_DELETE}, at={shot: 200.0})

    adopted = projects.adopt_subfolder_reviews(trip / "xt5", out)

    assert adopted["photos"] == 1
    assert load_decisions(out)[shot] == TO_DELETE
    # The copy keeps its source's stamp, so it does not pose as newer.
    assert load_decision_times(out)[shot] == 200.0


def test_an_older_trip_decision_does_not_replace_a_newer_camera_one(trip):
    shot = str(trip / "xt5" / "DSCF1.JPG")
    out = _child_out(trip / "xt5")
    save_decisions(out, {shot: KEPT}, at={shot: 200.0})
    save_decisions(_child_out(trip), {shot: TO_DELETE}, at={shot: 100.0})

    assert projects.adopt_subfolder_reviews(trip / "xt5", out)["photos"] == 0
    assert load_decisions(out)[shot] == KEPT


def test_a_newer_camera_decision_replaces_an_older_trip_one(trip):
    """The rule is the same in both directions: the trip's own no longer wins
    outright once the camera folder has been decided since."""
    shot = str(trip / "xt5" / "DSCF1.JPG")
    save_decisions(_child_out(trip / "xt5"), {shot: FAVORITE}, at={shot: 200.0})
    out = _child_out(trip)
    save_decisions(out, {shot: TO_DELETE}, at={shot: 100.0})

    projects.adopt_subfolder_reviews(trip, out)

    assert load_decisions(out)[shot] == FAVORITE


def test_an_unstamped_decision_is_older_than_a_stamped_one(trip):
    """Decisions from before stamps existed lose to any made since."""
    shot = str(trip / "xt5" / "DSCF1.JPG")
    out = _child_out(trip / "xt5")
    save_decisions(out, {shot: KEPT}, at={shot: None})
    save_decisions(_child_out(trip), {shot: TO_DELETE}, at={shot: 100.0})

    projects.adopt_subfolder_reviews(trip / "xt5", out)

    assert load_decisions(out)[shot] == TO_DELETE


def test_two_unstamped_decisions_keep_the_opened_projects_own(trip):
    shot = str(trip / "xt5" / "DSCF1.JPG")
    out = _child_out(trip / "xt5")
    save_decisions(out, {shot: KEPT}, at={shot: None})
    save_decisions(_child_out(trip), {shot: TO_DELETE}, at={shot: None})

    assert projects.adopt_subfolder_reviews(trip / "xt5", out)["photos"] == 0
    assert load_decisions(out)[shot] == KEPT


def test_an_applied_delete_beats_a_newer_keep(trip):
    """`deleted` is a fact about the drive, not an opinion: the file is gone."""
    shot = str(trip / "xt5" / "DSCF1.JPG")
    out = _child_out(trip / "xt5")
    save_decisions(out, {shot: KEPT}, at={shot: 200.0})
    save_decisions(_child_out(trip), {shot: DELETED}, at={shot: 100.0})

    projects.adopt_subfolder_reviews(trip / "xt5", out)

    assert load_decisions(out)[shot] == DELETED


def test_an_applied_delete_is_never_replaced(trip):
    shot = str(trip / "xt5" / "DSCF1.JPG")
    out = _child_out(trip / "xt5")
    save_decisions(out, {shot: DELETED}, at={shot: 100.0})
    save_decisions(_child_out(trip), {shot: KEPT}, at={shot: 200.0})

    projects.adopt_subfolder_reviews(trip / "xt5", out)

    assert load_decisions(out)[shot] == DELETED


def test_opening_each_side_in_turn_settles(trip):
    """Adoption runs on every open of either project; the copied stamp is what
    stops the two from trading the decision back and forth."""
    shot = str(trip / "xt5" / "DSCF1.JPG")
    camera_out = _child_out(trip / "xt5")
    trip_out = _child_out(trip)
    save_decisions(trip_out, {shot: TO_DELETE})

    projects.adopt_subfolder_reviews(trip / "xt5", camera_out)
    assert projects.adopt_subfolder_reviews(trip, trip_out)["photos"] == 0
    assert projects.adopt_subfolder_reviews(trip / "xt5", camera_out)["photos"] == 0

    # Changing it in the camera folder carries back up on the trip's next open.
    save_decisions(camera_out, {shot: KEPT})
    assert projects.adopt_subfolder_reviews(trip, trip_out)["photos"] == 1
    assert load_decisions(trip_out)[shot] == KEPT


def test_a_folder_sharing_a_name_prefix_is_not_inside(trip):
    """`xt5b/` is a sibling of `xt5/`, not part of it."""
    save_decisions(_child_out(trip), {str(trip / "xt5b" / "DSCF1.JPG"): KEPT})
    out = _child_out(trip / "xt5")

    assert projects.adopt_subfolder_reviews(trip / "xt5", out)["photos"] == 0


def test_a_nested_import_folder_adopts_from_its_trip(trip):
    nested = trip / "canon" / "02-06"
    nested.mkdir(parents=True)
    shot = str(nested / "IMG_1.JPG")
    save_decisions(_child_out(trip), {shot: FAVORITE})
    out = _child_out(nested)

    assert projects.adopt_subfolder_reviews(nested, out)["photos"] == 1
    assert load_decisions(out)[shot] == FAVORITE


def test_a_city_folder_adopts_from_its_city_project(trip):
    """Japan 2024 is reviewed per city, so the enclosing project for
    `canon/01_Hakodate` is the trip's Hakodate project, not the whole trip."""
    for city in ("01_Hakodate", "02_Sapporo"):
        (trip / "canon" / city).mkdir(parents=True)
    hakodate = trip / "canon" / "01_Hakodate"
    shot = str(hakodate / "IMG_1.JPG")
    save_decisions(_child_out_city(trip, "01_Hakodate"), {shot: TO_DELETE})
    out = _child_out(hakodate)

    assert projects.adopt_subfolder_reviews(hakodate, out)["photos"] == 1
    assert load_decisions(out)[shot] == TO_DELETE


def _child_out_city(folder, city):
    out = projects.project_output_dir(folder, city)
    out.mkdir(parents=True, exist_ok=True)
    return out


def test_video_tags_and_clips_come_down_from_the_trip(trip):
    clip = str(trip / "xt5" / "DSCF4470.MOV")
    trip_out = _child_out(trip)
    save_video_tags(trip_out, {"schema_version": 1, "tags": ["b-roll"],
                               "videos": {clip: "b-roll"}})
    save_user_clips(trip_out, clip, [{"start": 0.7, "end": 4.0}])
    out = _child_out(trip / "xt5")

    adopted = projects.adopt_subfolder_reviews(trip / "xt5", out)

    assert adopted["video_tags"] == 1 and adopted["clips"] == 1
    assert load_video_tags(out)["videos"][clip] == "b-roll"
    assert load_user_clips(out)[clip]["clips"] == [{"start": 0.7, "end": 4.0}]


def test_opening_a_camera_folder_adopts_from_its_trip(trip, monkeypatch):
    import server

    shot = str(trip / "xt5" / "a.JPG")
    save_decisions(_child_out(trip), {shot: TO_DELETE})
    monkeypatch.setattr(server, "project_output_dir", projects.project_output_dir)
    monkeypatch.setattr(server, "upsert_recent", lambda *a, **k: None)

    server.open_project(server.FolderRequest(folder=str(trip / "xt5")))

    assert load_decisions(projects.project_output_dir(trip / "xt5")) == {shot: TO_DELETE}

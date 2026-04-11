"""Reusable Streamlit UI components for photo curation.

Photo-maximizing layout: large images, minimal chrome, keeper-selection model.
"""

import streamlit as st
from PIL import Image


# ---------------------------------------------------------------------------
# Cluster header / navigation
# ---------------------------------------------------------------------------
def render_cluster_header(
    cluster: dict, cluster_idx: int, total_clusters: int
) -> int:
    """Compact cluster navigation bar. Returns the (possibly changed) cluster index."""
    n_images = len(cluster["images"])
    cols = st.columns([1, 4, 1])

    with cols[0]:
        if st.button("⬅ Prev", disabled=(cluster_idx == 0), key="nav_prev"):
            return max(0, cluster_idx - 1)

    with cols[1]:
        labels = [f"Cluster {i}" for i in range(total_clusters)]
        new_idx = st.selectbox(
            "cluster_nav",
            range(total_clusters),
            index=cluster_idx,
            format_func=lambda i: labels[i],
            label_visibility="collapsed",
            key="cluster_dropdown",
        )
        if new_idx != cluster_idx:
            return new_idx

    with cols[2]:
        if st.button("Next ➡", disabled=(cluster_idx >= total_clusters - 1), key="nav_next"):
            return min(total_clusters - 1, cluster_idx + 1)

    return cluster_idx


# ---------------------------------------------------------------------------
# Cluster grid — "select keepers" model
# ---------------------------------------------------------------------------
def render_cluster_grid(images: list[dict], cluster_id: int) -> None:
    """Display images in a responsive grid. Users click to KEEP; unselected = delete.

    Rank 1 image is pre-selected as a keeper by default.
    """
    n = len(images)
    if n == 0:
        st.info("No images in this cluster.")
        return

    # Adaptive columns
    n_cols = min(n, 4) if n > 2 else n

    # Initialise keeper state — rank 1 pre-selected
    for idx, img in enumerate(images):
        key = f"keep_{cluster_id}_{idx}"
        if key not in st.session_state:
            st.session_state[key] = img["rank"] == 1

    # Render rows
    for row_start in range(0, n, n_cols):
        row_images = images[row_start : row_start + n_cols]
        cols = st.columns(n_cols)
        for col_offset, img_data in enumerate(row_images):
            idx = row_start + col_offset
            path = img_data["path"]
            score = img_data["score"]
            rank = img_data["rank"]
            keep_key = f"keep_{cluster_id}_{idx}"
            is_kept = st.session_state[keep_key]

            with cols[col_offset]:
                try:
                    pil_img = Image.open(path)
                    st.image(pil_img, use_container_width=True)
                except FileNotFoundError:
                    st.error(f"Missing: {path}")
                    continue

                # Keep toggle
                st.session_state[keep_key] = st.checkbox(
                    f"⭐ Keep" if is_kept else "Keep",
                    value=is_kept,
                    key=f"cb_keep_{cluster_id}_{idx}",
                )

                # Compact info line
                status = "✅ keeping" if st.session_state[keep_key] else "🗑️ will delete"
                st.caption(f"#{rank}  score {score:.2f}  —  {status}")


# ---------------------------------------------------------------------------
# Compare / tournament mode
# ---------------------------------------------------------------------------
def render_compare_tournament(images: list[dict], cluster_id: int) -> None:
    """Full-width compare view with tournament (auto-pair) and manual modes."""
    if len(images) < 2:
        st.info("Need at least 2 images to compare.")
        return

    mode = st.radio(
        "Compare mode",
        ["Tournament", "Manual"],
        horizontal=True,
        key=f"compare_radio_{cluster_id}",
    )

    if mode == "Tournament":
        _render_tournament(images, cluster_id)
    else:
        _render_manual_compare(images, cluster_id)


def _render_tournament(images: list[dict], cluster_id: int) -> None:
    """Step through pairs; user picks the winner (loser marked for deletion)."""
    # Build pair list
    pairs = []
    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            pairs.append((i, j))

    match_key = f"tournament_match_{cluster_id}"
    if match_key not in st.session_state:
        st.session_state[match_key] = 0

    match_idx = st.session_state[match_key]
    total = len(pairs)

    if match_idx >= total:
        st.success("🏆 Tournament complete! Review your keeper selections above.")
        if st.button("🔄 Restart tournament", key=f"restart_tournament_{cluster_id}"):
            st.session_state[match_key] = 0
            st.rerun()
        return

    st.caption(f"Match {match_idx + 1} / {total}")
    idx_a, idx_b = pairs[match_idx]
    img_a, img_b = images[idx_a], images[idx_b]

    col1, col2 = st.columns(2)
    with col1:
        try:
            st.image(Image.open(img_a["path"]), use_container_width=True)
        except FileNotFoundError:
            st.error("Missing")
        st.caption(f"#{img_a['rank']}  score {img_a['score']:.2f}")
        if st.button("✅ Keep this one", key=f"tour_keep_a_{cluster_id}_{match_idx}"):
            # Keep A, mark B for deletion
            st.session_state[f"keep_{cluster_id}_{idx_a}"] = True
            st.session_state[f"keep_{cluster_id}_{idx_b}"] = False
            st.session_state[match_key] = match_idx + 1
            st.rerun()

    with col2:
        try:
            st.image(Image.open(img_b["path"]), use_container_width=True)
        except FileNotFoundError:
            st.error("Missing")
        st.caption(f"#{img_b['rank']}  score {img_b['score']:.2f}")
        if st.button("✅ Keep this one", key=f"tour_keep_b_{cluster_id}_{match_idx}"):
            # Keep B, mark A for deletion
            st.session_state[f"keep_{cluster_id}_{idx_b}"] = True
            st.session_state[f"keep_{cluster_id}_{idx_a}"] = False
            st.session_state[match_key] = match_idx + 1
            st.rerun()

    # Skip button
    if st.button("⏭ Skip this match", key=f"tour_skip_{cluster_id}_{match_idx}"):
        st.session_state[match_key] = match_idx + 1
        st.rerun()


def _render_manual_compare(images: list[dict], cluster_id: int) -> None:
    """Pick any two images to compare, then choose which to delete."""
    labels = [f"#{img['rank']} — {img['path'].split('/')[-1]} (score {img['score']:.2f})" for img in images]

    sel_col1, sel_col2 = st.columns(2)
    with sel_col1:
        pick_a = st.selectbox("Image A", labels, index=0, key=f"manual_a_{cluster_id}")
    with sel_col2:
        default_b = min(1, len(labels) - 1)
        pick_b = st.selectbox("Image B", labels, index=default_b, key=f"manual_b_{cluster_id}")

    idx_a = labels.index(pick_a)
    idx_b = labels.index(pick_b)

    if idx_a == idx_b:
        st.warning("Select two different images.")
        return

    col1, col2 = st.columns(2)
    with col1:
        try:
            st.image(Image.open(images[idx_a]["path"]), use_container_width=True)
        except FileNotFoundError:
            st.error("Missing")
        st.caption(f"#{images[idx_a]['rank']}  score {images[idx_a]['score']:.2f}")
        if st.button("✅ Keep A, delete B", key=f"man_keep_a_{cluster_id}"):
            st.session_state[f"keep_{cluster_id}_{idx_a}"] = True
            st.session_state[f"keep_{cluster_id}_{idx_b}"] = False
            st.rerun()

    with col2:
        try:
            st.image(Image.open(images[idx_b]["path"]), use_container_width=True)
        except FileNotFoundError:
            st.error("Missing")
        st.caption(f"#{images[idx_b]['rank']}  score {images[idx_b]['score']:.2f}")
        if st.button("✅ Keep B, delete A", key=f"man_keep_b_{cluster_id}"):
            st.session_state[f"keep_{cluster_id}_{idx_b}"] = True
            st.session_state[f"keep_{cluster_id}_{idx_a}"] = False
            st.rerun()

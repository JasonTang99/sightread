"""Reusable Streamlit UI components for photo curation."""

import streamlit as st
from PIL import Image

from utils import delete_image_safe


def render_cluster_grid(images: list[dict], cluster_id: int) -> None:
    """Display images in a 4-column grid with rank, score, and keep/delete buttons."""
    cols = st.columns(min(len(images), 4))

    for idx, img_data in enumerate(images):
        col = cols[idx % 4]
        path = img_data["path"]
        score = img_data["score"]
        rank = img_data["rank"]
        is_best = rank == 1
        delete_key = f"del_{cluster_id}_{idx}"

        with col:
            try:
                image = Image.open(path)
                st.image(image, use_container_width=True)
            except FileNotFoundError:
                st.error(f"Missing: {path}")
                continue

            label = f"**Rank {rank}** — Score: {score:.2f}"
            if is_best:
                label = f"⭐ BEST  |  {label}"
            st.markdown(label)

            # Per-image delete/keep toggle
            selected_key = f"select_{cluster_id}_{idx}"
            if selected_key not in st.session_state:
                st.session_state[selected_key] = False

            st.session_state[selected_key] = st.checkbox(
                "Mark for deletion",
                value=st.session_state[selected_key],
                key=delete_key,
            )


def render_compare_mode(images: list[dict]) -> None:
    """Side-by-side comparison of two selected images."""
    if len(images) < 2:
        st.info("Need at least 2 images to compare.")
        return

    labels = [f"Rank {img['rank']} — {img['path']}" for img in images]

    col1, col2 = st.columns(2)
    with col1:
        pick_a = st.selectbox("Image A", labels, index=0, key="compare_a")
    with col2:
        pick_b = st.selectbox("Image B", labels, index=1, key="compare_b")

    idx_a = labels.index(pick_a)
    idx_b = labels.index(pick_b)

    col1, col2 = st.columns(2)
    with col1:
        try:
            st.image(Image.open(images[idx_a]["path"]), use_container_width=True)
            st.caption(f"Score: {images[idx_a]['score']:.2f}")
        except FileNotFoundError:
            st.error("Image not found")
    with col2:
        try:
            st.image(Image.open(images[idx_b]["path"]), use_container_width=True)
            st.caption(f"Score: {images[idx_b]['score']:.2f}")
        except FileNotFoundError:
            st.error("Image not found")


def render_auto_select(images: list[dict], cluster_id: int) -> None:
    """Slider to auto-select images below a score threshold for deletion."""
    scores = [img["score"] for img in images]
    min_score, max_score = min(scores), max(scores)

    threshold = st.slider(
        "Auto-select images with score below:",
        min_value=0.0,
        max_value=1.0,
        value=min_score,
        step=0.01,
        key=f"threshold_{cluster_id}",
    )

    count = 0
    for idx, img in enumerate(images):
        selected_key = f"select_{cluster_id}_{idx}"
        if img["score"] < threshold:
            st.session_state[selected_key] = True
            count += 1
        # Don't auto-deselect manually marked images

    if count:
        st.warning(f"{count} image(s) auto-selected for deletion (score < {threshold:.2f})")


def render_keep_best(images: list[dict], cluster_id: int) -> None:
    """Button to mark all non-best images for deletion."""
    if st.button("🏆 Keep Best Only", key=f"keep_best_{cluster_id}"):
        for idx, img in enumerate(images):
            selected_key = f"select_{cluster_id}_{idx}"
            st.session_state[selected_key] = img["rank"] != 1
        st.rerun()

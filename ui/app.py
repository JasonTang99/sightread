"""📸 Photo Curation Tool — main Streamlit application.

Minimal-chrome, photo-maximizing UI.  One cluster at a time; select keepers,
compare/tournament mode, confirm & advance.
"""

import os
import sys

import streamlit as st

# Ensure ui/ is on the import path
sys.path.insert(0, os.path.dirname(__file__))

from components import render_cluster_grid, render_cluster_header, render_compare_tournament
from utils import delete_image_safe, load_results, remove_images_from_results, save_results

# ---------------------------------------------------------------------------
# Page config & custom CSS (reduce chrome, maximise photos)
# ---------------------------------------------------------------------------
st.set_page_config(page_title="📸 Sightread", layout="wide", initial_sidebar_state="collapsed")

st.markdown(
    """
    <style>
    /* Shrink default Streamlit top padding */
    .block-container { padding-top: 1rem !important; padding-bottom: 0.5rem !important; }
    /* Compact header */
    header[data-testid="stHeader"] { height: 2.5rem; }
    /* Tighter image margins */
    [data-testid="stImage"] { margin-bottom: 0.25rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------------------
# Load data
# ---------------------------------------------------------------------------
RESULTS_PATH = "outputs/results.json"

if not os.path.exists(RESULTS_PATH):
    st.error(
        f"No results file at `{RESULTS_PATH}`.  "
        "Run the pipeline first:\n\n"
        "```bash\npython scripts/pipeline.py --image-dir /path/to/photos\n```"
    )
    st.stop()

data = load_results(RESULTS_PATH)
clusters = data["clusters"]

if not clusters:
    st.success("🎉 All clusters processed! No images left to curate.")
    st.stop()

# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
if "cluster_idx" not in st.session_state:
    st.session_state.cluster_idx = 0

# Clamp index in case clusters were removed
st.session_state.cluster_idx = min(st.session_state.cluster_idx, len(clusters) - 1)

cluster_idx = st.session_state.cluster_idx
cluster = clusters[cluster_idx]
images = cluster["images"]
cluster_id = cluster["cluster_id"]

# ---------------------------------------------------------------------------
# Compact top bar: title + cluster navigation
# ---------------------------------------------------------------------------
title_col, nav_col = st.columns([1, 3])
with title_col:
    st.markdown("#### 📸 Sightread")
with nav_col:
    new_idx = render_cluster_header(cluster, cluster_idx, len(clusters))
    if new_idx != cluster_idx:
        st.session_state.cluster_idx = new_idx
        st.rerun()

st.caption(f"{len(images)} images  ·  best: {cluster['best_image'].split('/')[-1]}")

# ---------------------------------------------------------------------------
# View toggle: Grid vs Compare
# ---------------------------------------------------------------------------
view = st.radio("View", ["Grid", "Compare"], horizontal=True, label_visibility="collapsed", key="view_toggle")

if view == "Grid":
    render_cluster_grid(images, cluster_id)
else:
    render_compare_tournament(images, cluster_id)

# ---------------------------------------------------------------------------
# Summary + action bar
# ---------------------------------------------------------------------------
st.divider()

n_keep = sum(1 for i in range(len(images)) if st.session_state.get(f"keep_{cluster_id}_{i}", False))
n_delete = len(images) - n_keep

info_col, action_col = st.columns([3, 2])
with info_col:
    if n_delete > 0:
        st.warning(f"Keeping **{n_keep}** of **{len(images)}** — **{n_delete}** will be moved to trash")
    else:
        st.info(f"Keeping all **{len(images)}** images")

with action_col:
    btn_cols = st.columns(3)

    # Keep Best Only
    with btn_cols[0]:
        if st.button("🏆 Keep Best", key="keep_best_btn"):
            for idx, img in enumerate(images):
                st.session_state[f"keep_{cluster_id}_{idx}"] = img["rank"] == 1
            st.rerun()

    # Skip
    with btn_cols[1]:
        if st.button("⏭ Skip", key="skip_btn"):
            if cluster_idx < len(clusters) - 1:
                st.session_state.cluster_idx = cluster_idx + 1
                st.rerun()
            else:
                st.toast("Last cluster — nothing to skip to")

    # Confirm & Next
    with btn_cols[2]:
        if st.button("✅ Confirm", type="primary", key="confirm_btn"):
            paths_to_delete = set()
            for idx, img in enumerate(images):
                if not st.session_state.get(f"keep_{cluster_id}_{idx}", False):
                    paths_to_delete.add(img["path"])

            if paths_to_delete:
                moved = 0
                for p in paths_to_delete:
                    try:
                        delete_image_safe(p)
                        moved += 1
                    except FileNotFoundError:
                        pass
                # Update and save results
                remove_images_from_results(data, paths_to_delete)
                save_results(data)
                st.toast(f"Moved {moved} image(s) to trash")

            # Advance to next cluster (or stay if last)
            clusters_after = data["clusters"]
            if cluster_idx < len(clusters_after) - 1:
                st.session_state.cluster_idx = cluster_idx + 1
            elif clusters_after:
                st.session_state.cluster_idx = len(clusters_after) - 1
            else:
                st.session_state.cluster_idx = 0
            st.rerun()

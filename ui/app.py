"""📸 Photo Curation Tool — main Streamlit application."""

import os
import sys

import streamlit as st

# Run from repo root so relative image paths resolve correctly.
# Also add ui/ to the Python path for local imports.
sys.path.insert(0, os.path.dirname(__file__))

from components import (
    render_auto_select,
    render_cluster_grid,
    render_compare_mode,
    render_keep_best,
)
from utils import delete_image_safe, load_results

st.set_page_config(page_title="Photo Curation Tool", layout="wide")
st.title("📸 Photo Curation Tool")

# --- Load data ---
RESULTS_PATH = "outputs/results.json"

if not os.path.exists(RESULTS_PATH):
    st.error(
        f"Results file not found at `{RESULTS_PATH}`. "
        "Run `python scripts/generate_sample_data.py` first."
    )
    st.stop()

data = load_results(RESULTS_PATH)
clusters = data["clusters"]

# --- Sidebar: cluster selector ---
st.sidebar.header("Clusters")
cluster_labels = [f"Cluster {c['cluster_id']} ({len(c['images'])} images)" for c in clusters]
selected_idx = st.sidebar.selectbox(
    "Select cluster", range(len(clusters)), format_func=lambda i: cluster_labels[i]
)

cluster = clusters[selected_idx]
images = cluster["images"]
cluster_id = cluster["cluster_id"]

st.sidebar.markdown("---")
st.sidebar.markdown(f"**Best image:** `{cluster['best_image']}`")

# --- Main area: Cluster grid ---
st.header(f"Cluster {cluster_id}")
render_cluster_grid(images, cluster_id)

# --- Bulk delete selected ---
selected_paths = []
for idx, img in enumerate(images):
    key = f"select_{cluster_id}_{idx}"
    if st.session_state.get(key, False):
        selected_paths.append(img["path"])

if selected_paths:
    st.warning(f"**{len(selected_paths)}** image(s) marked for deletion.")
    if st.button("🗑️ Delete Selected Images", type="primary"):
        moved = []
        for path in selected_paths:
            try:
                dest = delete_image_safe(path)
                moved.append(dest)
            except FileNotFoundError:
                st.error(f"Already missing: {path}")
        if moved:
            st.success(f"Moved {len(moved)} image(s) to trash.")
            # Clear selection state
            for idx in range(len(images)):
                key = f"select_{cluster_id}_{idx}"
                st.session_state[key] = False
            st.rerun()

st.divider()

# --- Compare mode ---
with st.expander("🔍 Compare Mode"):
    render_compare_mode(images)

st.divider()

# --- Auto-select worst ---
with st.expander("⚡ Auto-select by Score Threshold"):
    render_auto_select(images, cluster_id)

# --- Keep best only ---
render_keep_best(images, cluster_id)

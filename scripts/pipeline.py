#!/usr/bin/env python3
"""
DINOv3 + two-stage clustering + IQA ensemble photo curation pipeline.

Usage:
    python scripts/pipeline.py --image-dir /path/to/photos
    python scripts/pipeline.py --image-dir /path/to/photos --output-dir outputs

Without --output-dir, results go to the per-project data directory
(~/.local/share/sightread/projects/<md5>) — the same place the webapp reads.
"""

import argparse
import gc
import json
import os
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

import numpy as np
import torch
from PIL import Image, ExifTags
from tqdm import tqdm

# thumbs.py and media.py are the webapp modules scripts may import: they pull
# in nothing beyond PIL, pillow-heif and the stdlib. Duplicating the thumbnail
# cache key instead is how the pipeline's output silently stops being a cache
# hit, and duplicating the extension sets is how a format gets scanned here and
# forgotten by the export. Importing thumbs also registers the HEIF opener.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))
import media  # noqa: E402
import thumbs  # noqa: E402

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_NAME = "facebook/dinov3-vitl16-pretrain-lvd1689m"
BATCH_SIZE = 32
NUM_WORKERS = 4

# Two-stage clustering thresholds (cosine distance)
TIGHT_THRESHOLD = 0.08   # near-duplicate / burst
LOOSE_THRESHOLD = 0.22   # same-scene
BURST_WINDOW_S = 3.0     # EXIF timestamp delta to pre-group
MAX_CLUSTER_GAP_S = 3600.0  # max EXIF gap within a cluster (1 hr)

# Re-shoot merge: same subject framed portrait *and* landscape lands in two
# clusters because rotating the camera moves the embedding further than the
# same-scene threshold. Merge those back when they are close in time.
ORIENT_MERGE_WINDOW_S = 120.0  # max EXIF gap between the two framings
ORIENT_MERGE_THRESHOLD = 0.38  # cosine dist between cluster centroids

# The same shot taken on two cameras is the other re-shoot: different sensor,
# lens and processing move the embedding the way rotating the camera does, and
# the two never land in one cluster on their own. It takes longer to raise the
# second camera than to turn the first, so it gets its own wider window —
# measured on 2026_01_Japan, where the phone/camera pairs of one subject sit
# 126-331s apart. The distance ceiling is the *same-scene* threshold, not the
# looser rotation one: two framings of one subject genuinely sit further apart
# than two devices pointed at it, and at 0.38 same-framing merges started
# swallowing neighbouring compositions (a 12-photo riverbed group on the Hoh
# trip that held three different subjects).
CROSS_DEVICE_WINDOW_S = 300.0
CROSS_DEVICE_THRESHOLD = LOOSE_THRESHOLD

# Score weights (ensemble)
SCORE_WEIGHTS = {
    "musiq": 0.35,
    "nima": 0.25,
    "clipiqa+": 0.20,
    "laion_aes": 0.10,
    "sharpness": 0.10,
}
EXPOSURE_PENALTY_WEIGHT = 0.15
FACE_BONUS_WEIGHT = 0.10

IMAGE_EXTENSIONS = media.IMAGE_EXTENSIONS
VIDEO_EXTENSIONS = media.VIDEO_EXTENSIONS
# FAISS k-NN connectivity replaces O(n²) sklearn distance matrix above this size
_FAISS_N_THRESHOLD = 5_000
_FAISS_K_NEIGHBORS = 50     # neighbors per point for connectivity graph
# Scoring batch size and resize for uniform GPU batching
SCORE_BATCH_SIZE = 16
SCORE_RESIZE = 512           # resize to this before neural metrics
_EXIF_DATETIME_TAG = next(k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal")
_EXIF_ORIENTATION_TAG = 0x0112
_EXIF_MODEL_TAG = 0x0110


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def _scan_image_paths(image_dir: str) -> list[str]:
    root = Path(image_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Image directory not found: {root}")
    return sorted(
        str(Path(dirpath, name))
        for dirpath, names in media.walk_media(root)
        for name in names
        if media.is_image(name)
    )


def _parse_exif_timestamp(exif) -> float | None:
    """Return EXIF DateTimeOriginal as unix seconds, or None."""
    raw = None
    try:
        ifd = exif.get_ifd(ExifTags.IFD.Exif)
        raw = ifd.get(_EXIF_DATETIME_TAG)
    except Exception:
        pass
    if not raw:
        raw = exif.get(_EXIF_DATETIME_TAG)
    if not raw:
        # Fallback to DateTime (0x0132) top-level
        raw = exif.get(0x0132)
    if not raw:
        return None
    try:
        return datetime.strptime(raw, "%Y:%m:%d %H:%M:%S").timestamp()
    except ValueError:
        return None


class ShotMeta(NamedTuple):
    timestamp: float | None
    # The *displayed* framing: EXIF Orientation 5-8 rotates by 90°, which
    # swaps the stored width and height.
    framing: str  # portrait | landscape | square | unknown
    # EXIF Model, e.g. "X-T5" or "iPhone 16 Pro". A trip opened as one project
    # mixes cameras, and the folder a photo sits in only names who handed the
    # files over: "google photos" alone holds six models on the Hoh trip.
    model: str | None


def _read_exif_meta(path: str) -> ShotMeta:
    try:
        with Image.open(path) as img:
            width, height = img.size
            exif = img.getexif()
            ts = _parse_exif_timestamp(exif) if exif else None
            model = exif.get(_EXIF_MODEL_TAG) if exif else None
            if exif and exif.get(_EXIF_ORIENTATION_TAG) in (5, 6, 7, 8):
                width, height = height, width
    except Exception:
        return ShotMeta(None, "unknown", None)
    if isinstance(model, str):
        model = model.strip() or None
    else:
        model = None
    if width == height:
        return ShotMeta(ts, "square", model)
    return ShotMeta(ts, "portrait" if height > width else "landscape", model)


def load_paths_and_meta(
    image_dir: str,
) -> tuple[list[str], list[float | None], list[str], list[str | None]]:
    paths = _scan_image_paths(image_dir)
    if not paths:
        raise RuntimeError(f"No images found in {image_dir}")
    meta = [_read_exif_meta(p) for p in tqdm(paths, desc="Reading EXIF")]
    timestamps = [m.timestamp for m in meta]
    orientations = [m.framing for m in meta]
    models = [m.model for m in meta]
    print(f"Found {len(paths)} images ({sum(t is not None for t in timestamps)} with EXIF timestamps)")
    seen = sorted({m for m in models if m})
    if len(seen) > 1:
        print(f"Cameras: {', '.join(seen)}")
    return paths, timestamps, orientations, models


def _day_chunks(
    paths: list[str], timestamps: list[float | None] | None
) -> list[tuple[str, list[str]]]:
    """Split photos into one chunk per shooting day, in day order.

    The expensive stages checkpoint their cache after each chunk. A 9,500-photo
    trip scores for two hours, and the cache used to be written only once the
    whole stage finished — a crash at 90% threw all of it away. A day is the
    natural unit: nothing clusters across a one-hour gap, let alone a night.
    Undated photos trail as one last chunk. Input order is kept within a day.
    """
    days: dict[str, list[str]] = {}
    undated: list[str] = []
    for i, p in enumerate(paths):
        t = timestamps[i] if timestamps is not None else None
        if t is None:
            undated.append(p)
        else:
            days.setdefault(datetime.fromtimestamp(t).date().isoformat(), []).append(p)
    chunks = sorted(days.items())
    if undated:
        chunks.append(("undated", undated))
    return chunks


def _load_path_cache(cache_path: Path, paths: list[str], load) -> dict | None:
    """Rows of a path-keyed cache, or None when it cannot be used incrementally.

    Unusable means absent, unreadable, holding photos no longer in the folder
    (the pre-existing rule: that triggers a full recompute), or — new with
    checkpointing, which rewrites the pair many times a run — a data file whose
    row count disagrees with its sidecar, which would map rows to the wrong
    photos.
    """
    sidecar = cache_path.with_suffix(".paths.json")
    if not (cache_path.exists() and sidecar.exists()):
        return None
    try:
        cached_paths = json.loads(sidecar.read_text())
        rows = load(cache_path)
    except Exception:
        return None
    if not set(cached_paths) <= set(paths):
        return None
    if any(len(v) != len(cached_paths) for v in rows.values()):
        warnings.warn(f"{cache_path.name} does not match its sidecar — recomputing")
        return None
    return {k: dict(zip(cached_paths, v)) for k, v in rows.items()}


def _write_path_cache(cache_path: Path, paths: list[str], write) -> None:
    """Write a data file and its paths sidecar, each via rename.

    `write(fileobj)` writes the data. The data file is replaced first, so a
    crash between the two renames leaves a longer data file than sidecar, which
    _load_path_cache rejects rather than misreads.
    """
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar = cache_path.with_suffix(".paths.json")
    tmp_data = cache_path.with_name(cache_path.name + ".tmp")
    tmp_side = sidecar.with_name(sidecar.name + ".tmp")
    with open(tmp_data, "wb") as f:
        write(f)
    tmp_side.write_text(json.dumps(paths))
    os.replace(tmp_data, cache_path)
    os.replace(tmp_side, sidecar)


def _release_gpu() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


# ---------------------------------------------------------------------------
# Step 1: Embeddings (mean-pool patch tokens + CLS concat, flip TTA, parallel decode)
# ---------------------------------------------------------------------------
class _ImageDataset(torch.utils.data.Dataset):
    def __init__(self, paths, processor):
        self.paths = paths
        self.processor = processor

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, idx):
        try:
            img = Image.open(self.paths[idx]).convert("RGB")
            img.load()
        except Exception as exc:
            warnings.warn(f"Decode failed {self.paths[idx]}: {exc}")
            img = Image.new("RGB", (224, 224))
        tensor = self.processor(images=img, return_tensors="pt")["pixel_values"][0]
        return tensor


def _extract_features(model, pixel_values, num_skip_tokens: int) -> torch.Tensor:
    """Return [B, 2*D] feature: concat(CLS, mean(patch tokens))."""
    outputs = model(pixel_values=pixel_values)
    hidden = outputs.last_hidden_state  # [B, T, D]
    cls = hidden[:, 0]
    patch = hidden[:, num_skip_tokens:].mean(dim=1)
    return torch.cat([cls, patch], dim=-1)


def _embed_with_model(
    paths: list[str],
    model,
    processor,
    num_skip: int,
    device: str = DEVICE,
    batch_size: int = BATCH_SIZE,
    num_workers: int = NUM_WORKERS,
    flip_tta: bool = False,
    desc: str | None = None,
) -> np.ndarray:
    """Embed paths with an already-loaded model. L2-normalized float32 [N, 2D]."""
    ds = _ImageDataset(paths, processor)
    loader = torch.utils.data.DataLoader(
        ds, batch_size=batch_size, num_workers=num_workers,
        pin_memory=(device == "cuda"), shuffle=False,
    )

    all_feats: list[np.ndarray] = []
    for batch in tqdm(loader, desc=desc or f"DINOv3 embeddings ({len(paths)} images)"):
        batch = batch.to(device, non_blocking=True)
        with torch.no_grad(), torch.amp.autocast(device_type=device if device != "cpu" else "cpu"):
            feats = _extract_features(model, batch, num_skip)
            if flip_tta:
                feats_flip = _extract_features(model, torch.flip(batch, dims=[-1]), num_skip)
                feats = (feats + feats_flip) * 0.5
        all_feats.append(feats.float().cpu().numpy())

    embeddings = np.concatenate(all_feats, axis=0).astype(np.float32)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1
    embeddings /= norms
    return embeddings


def _load_embedding_model(model_name: str = MODEL_NAME, device: str = DEVICE):
    """Load DINOv3 processor + model. Returns (model, processor, num_skip_tokens)."""
    from transformers import AutoImageProcessor, AutoModel

    processor = AutoImageProcessor.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name).to(device).eval()
    num_register = int(getattr(model.config, "num_register_tokens", 0) or 0)
    return model, processor, 1 + num_register


def compute_embeddings(
    paths: list[str],
    cache_path: Path,
    device: str = DEVICE,
    model_name: str = MODEL_NAME,
    batch_size: int = BATCH_SIZE,
    num_workers: int = NUM_WORKERS,
    flip_tta: bool = False,
    timestamps: list[float | None] | None = None,
) -> np.ndarray:
    """Compute or load cached embeddings.

    Incremental: only photos missing from the cache are embedded, one shooting
    day at a time, and the cache is written after every day.
    """
    cached = _load_path_cache(cache_path, paths, lambda f: {"emb": np.load(str(f))})
    done: dict[str, np.ndarray] = cached["emb"] if cached else {}
    todo = [i for i, p in enumerate(paths) if p not in done]

    if not todo:
        print(f"Loading cached embeddings ({len(paths)} paths)")
        return np.stack([done[p] for p in paths]).astype(np.float32)
    if done:
        print(f"Incremental embeddings: {len(done)} cached + {len(todo)} new")

    chunks = _day_chunks(
        [paths[i] for i in todo],
        [timestamps[i] for i in todo] if timestamps is not None else None,
    )
    model, processor, num_skip = _load_embedding_model(model_name, device)
    try:
        for day, chunk in chunks:
            embs = _embed_with_model(
                chunk, model, processor, num_skip,
                device=device, batch_size=batch_size, num_workers=num_workers, flip_tta=flip_tta,
                desc=f"DINOv3 embeddings {day} ({len(chunk)} images)",
            )
            done.update(zip(chunk, embs))
            have = [p for p in paths if p in done]
            _write_path_cache(
                cache_path, have,
                lambda f: np.save(f, np.stack([done[p] for p in have]).astype(np.float32)),
            )
    finally:
        del model, processor
        _release_gpu()

    # Remove stale .hash sidecar from old cache format
    old_hash = cache_path.with_suffix(".hash")
    if old_hash.exists():
        old_hash.unlink()
    print(f"Saved embeddings to {cache_path}  ({len(paths)} total, {len(chunks)} day checkpoint(s))")
    return np.stack([done[p] for p in paths]).astype(np.float32)


# ---------------------------------------------------------------------------
# Step 2: Clustering — EXIF burst pre-group → tight near-dup → loose same-scene merge
# ---------------------------------------------------------------------------
def _agglomerative_faiss(embeddings: np.ndarray, threshold: float) -> np.ndarray:
    """Agglomerative clustering via FAISS k-NN connectivity. O(n*k) memory."""
    import faiss
    from scipy.sparse import csr_matrix
    from sklearn.cluster import AgglomerativeClustering

    n, d = embeddings.shape
    k = min(_FAISS_K_NEIGHBORS + 1, n)  # +1: FAISS includes self in results

    index = faiss.IndexFlatIP(d)
    index.add(embeddings)
    _, indices = index.search(embeddings, k)

    rows, cols = [], []
    for i in range(n):
        for j in indices[i]:
            j = int(j)
            if j != i and j >= 0:
                rows.append(i)
                cols.append(j)
    data = np.ones(len(rows), dtype=np.float32)
    connectivity = csr_matrix((data, (rows, cols)), shape=(n, n))
    connectivity = connectivity.maximum(connectivity.T)

    return AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        metric="cosine",
        linkage="average",
        connectivity=connectivity,
    ).fit_predict(embeddings)


def _agglomerative(embeddings: np.ndarray, threshold: float) -> np.ndarray:
    n = len(embeddings)
    if n == 1:
        return np.array([0])
    if n > _FAISS_N_THRESHOLD:
        try:
            return _agglomerative_faiss(embeddings, threshold)
        except ImportError:
            warnings.warn(
                "faiss not found — falling back to O(n²) sklearn. Install faiss-cpu for large datasets."
            )
    from sklearn.cluster import AgglomerativeClustering
    return AgglomerativeClustering(
        n_clusters=None,
        distance_threshold=threshold,
        metric="cosine",
        linkage="average",
    ).fit_predict(embeddings)


def calibrate_threshold(embeddings: np.ndarray, fallback: float) -> float:
    """Pick valley between near-dup and noise in 1-NN distance histogram."""
    if len(embeddings) < 10:
        return fallback
    from sklearn.neighbors import NearestNeighbors
    n = len(embeddings)
    sample_size = min(n, 5000)
    if n > sample_size:
        rng = np.random.default_rng(42)
        idx = rng.choice(n, size=sample_size, replace=False)
        sample = embeddings[idx]
    else:
        sample = embeddings
    nn = NearestNeighbors(n_neighbors=2, metric="cosine", algorithm="brute").fit(sample)
    dists, _ = nn.kneighbors(sample)
    nn_dist = dists[:, 1]
    hist, edges = np.histogram(nn_dist, bins=40, range=(0.0, 0.5))
    # Find first local minimum after first local max
    peak = int(np.argmax(hist))
    valley = peak + 1
    while valley < len(hist) - 1 and hist[valley] >= hist[valley - 1]:
        valley += 1
    chosen = float((edges[valley] + edges[valley + 1]) * 0.5) if valley < len(hist) else fallback
    chosen = max(0.05, min(chosen, 0.35))
    print(f"Calibrated loose threshold: {chosen:.3f} (fallback {fallback})")
    return chosen


def _merge_reshoot_pairs(
    embeddings: np.ndarray,
    timestamps: list[float | None],
    orientations: list[str],
    labels: np.ndarray,
    window_s: float,
    threshold: float,
    max_span_s: float,
    models: list[str | None] | None = None,
    cross_window_s: float = 0.0,
    cross_threshold: float = CROSS_DEVICE_THRESHOLD,
) -> np.ndarray:
    """Merge clusters holding one subject shot twice — rotated, or on a second camera.

    Rotating the camera moves a photo further in embedding space than the
    same-scene threshold allows, so the two framings split apart. A pair is
    rejoined only when it is close in time, differs in framing, and the cluster
    centroids are still within `threshold`.

    Reaching for the other camera splits a subject the same way, and there the
    framing usually does *not* change — a phone held portrait and a camera held
    portrait — so framing alone can't be what licenses the merge. When `models`
    names two different cameras, a same-framing pair is allowed to join inside
    `cross_window_s` if its centroids are within the tighter `cross_threshold`.
    Passing no `models` (or leaving `cross_window_s` at 0) keeps the old
    framing-only behaviour.

    `threshold` is the loosest distance anywhere in the pipeline, so what a
    merged group is allowed to span matters as much as the pairing rule. Merges
    chain — A joins B, B joins C — and each link only has to be `window_s` from
    the last, so capping the span at the max cluster gap let a chain walk across
    a whole hour: on the Hoh trip that grew a 44-photo cluster of one viewpoint
    into 74 photos spanning 33 minutes. The span is capped at `window_s`
    instead, which is what "a re-shoot of the same subject" meant in the first
    place.
    """
    groups: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        groups.setdefault(int(lab), []).append(i)

    cross_window_s = cross_window_s if models is not None else 0.0
    info: dict[int, dict] = {}
    for lab, idxs in groups.items():
        ts = [timestamps[i] for i in idxs if timestamps[i] is not None]
        kinds = {orientations[i] for i in idxs if orientations[i] in ("portrait", "landscape")}
        # Without a timestamp there is no hint to merge on; without a known
        # framing there is nothing to pair across.
        if not ts or not kinds:
            continue
        centroid = embeddings[idxs].mean(axis=0)
        centroid /= max(np.linalg.norm(centroid), 1e-8)
        info[lab] = {
            "idxs": idxs,
            "t_min": min(ts),
            "t_max": max(ts),
            "kinds": kinds,
            "models": {models[i] for i in idxs if models[i]} if models else set(),
            "centroid": centroid,
        }

    ordered = sorted(info, key=lambda lab: info[lab]["t_min"])
    widest = max(window_s, cross_window_s)
    candidates: list[tuple[float, int, int, bool]] = []
    for pos, a in enumerate(ordered):
        left = info[a]
        for b in ordered[pos + 1:]:
            right = info[b]
            # `ordered` is sorted by t_min, so once one candidate is out of
            # range every later one is too.
            gap = right["t_min"] - left["t_max"]
            if gap > widest:
                break
            # Two clusters count as different cameras only when both name one
            # and the names don't overlap: an unknown model could be either.
            cross = bool(
                left["models"] and right["models"] and not (left["models"] & right["models"])
            )
            if gap > (cross_window_s if cross else window_s):
                continue
            same_framing = left["kinds"] == right["kinds"]
            if same_framing and not cross:
                continue
            dist = 1.0 - float(left["centroid"] @ right["centroid"])
            if dist <= (cross_threshold if same_framing else threshold):
                candidates.append((dist, a, b, same_framing))

    parent = {lab: lab for lab in info}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    span = {lab: (info[lab]["t_min"], info[lab]["t_max"]) for lab in info}
    merged = 0
    n_cross = 0
    for _dist, a, b, same_framing in sorted(candidates):
        ra, rb = find(a), find(b)
        if ra == rb:
            continue
        lo = min(span[ra][0], span[rb][0])
        hi = max(span[ra][1], span[rb][1])
        # A chain of re-shoots is still one re-shoot's worth of time.
        if max_span_s and max_span_s > 0 and hi - lo > max_span_s:
            continue
        parent[rb] = ra
        span[ra] = (lo, hi)
        merged += 1
        n_cross += same_framing

    if merged:
        for lab in info:
            root = find(lab)
            if root != lab:
                for i in info[lab]["idxs"]:
                    labels[i] = root
        detail = f" ({n_cross} of them same framing on a second camera)" if n_cross else ""
        print(f"Merged {merged} re-shoot cluster pair(s) shot within {widest}s{detail}")
    return labels


def cluster_embeddings(
    embeddings: np.ndarray,
    timestamps: list[float | None],
    orientations: list[str] | None = None,
    tight: float = TIGHT_THRESHOLD,
    loose: float = LOOSE_THRESHOLD,
    burst_window_s: float = BURST_WINDOW_S,
    max_gap_s: float = MAX_CLUSTER_GAP_S,
    orient_window_s: float = ORIENT_MERGE_WINDOW_S,
    orient_threshold: float = ORIENT_MERGE_THRESHOLD,
    auto_loose: bool = False,
    models: list[str | None] | None = None,
    cross_window_s: float = CROSS_DEVICE_WINDOW_S,
    cross_threshold: float = CROSS_DEVICE_THRESHOLD,
) -> dict[int, list[int]]:
    """Two-stage clustering: burst pre-group, tight dedup inside parent groups."""
    n = len(embeddings)
    if auto_loose:
        loose = calibrate_threshold(embeddings, loose)

    # Stage 1: loose same-scene grouping on embeddings (coarse parent)
    parent_labels = _agglomerative(embeddings, loose)

    # Stage 2: within each parent, refine with tight threshold AND timestamp burst
    final_labels = np.full(n, -1, dtype=np.int64)
    next_id = 0
    for parent in np.unique(parent_labels):
        members = np.where(parent_labels == parent)[0]
        if len(members) == 1:
            final_labels[members[0]] = next_id
            next_id += 1
            continue

        sub_embs = embeddings[members]
        sub_labels = _agglomerative(sub_embs, tight)

        # Fuse timestamp bursts: images within burst_window_s sharing parent get merged
        if any(timestamps[i] is not None for i in members):
            ts = np.array([timestamps[i] if timestamps[i] is not None else np.nan for i in members])
            order = np.argsort(np.where(np.isnan(ts), np.inf, ts))
            current = None
            prev_t = None
            for pos in order:
                t = ts[pos]
                if np.isnan(t):
                    break
                if current is None or (t - prev_t) > burst_window_s:
                    current = sub_labels[pos]
                else:
                    sub_labels[sub_labels == sub_labels[pos]] = current
                prev_t = t

        for sub in np.unique(sub_labels):
            idxs = members[sub_labels == sub]
            final_labels[idxs] = next_id
            next_id += 1

    # Stage 3: enforce max time gap within cluster — split if consecutive EXIF gap > max_gap_s
    if max_gap_s and max_gap_s > 0:
        groups: dict[int, list[int]] = {}
        for i, lab in enumerate(final_labels):
            groups.setdefault(int(lab), []).append(i)
        next_id = int(final_labels.max()) + 1
        for lab, idxs in groups.items():
            ts_pairs = [(i, timestamps[i]) for i in idxs if timestamps[i] is not None]
            if len(ts_pairs) < 2:
                continue
            ts_pairs.sort(key=lambda x: x[1])
            splits: list[list[int]] = [[ts_pairs[0][0]]]
            for prev, cur in zip(ts_pairs, ts_pairs[1:]):
                if cur[1] - prev[1] > max_gap_s:
                    splits.append([cur[0]])
                else:
                    splits[-1].append(cur[0])
            if len(splits) <= 1:
                continue
            # Keep first split under original label; assign later splits new ids.
            # Images without timestamps stay with first split.
            no_ts = [i for i in idxs if timestamps[i] is None]
            splits[0].extend(no_ts)
            for new_split in splits[1:]:
                for i in new_split:
                    final_labels[i] = next_id
                next_id += 1

    # Stage 4: rejoin the same subject shot twice — rotated, or on the other camera
    if orientations is not None and orient_window_s > 0:
        widest = max(orient_window_s, cross_window_s if models else 0.0)
        final_labels = _merge_reshoot_pairs(
            embeddings,
            timestamps,
            orientations,
            final_labels,
            window_s=orient_window_s,
            threshold=orient_threshold,
            # Never wider than the gap stage 3 just enforced: a merge that
            # re-joined two clusters it had split would undo that split.
            max_span_s=(min(widest, max_gap_s) if max_gap_s and max_gap_s > 0 else widest),
            models=models,
            cross_window_s=cross_window_s,
            cross_threshold=cross_threshold,
        )

    clusters: dict[int, list[int]] = {}
    for i, lab in enumerate(final_labels):
        clusters.setdefault(int(lab), []).append(i)
    print(f"Clustered {n} images into {len(clusters)} groups (tight={tight}, loose={loose}, max_gap={max_gap_s}s)")
    return clusters


# ---------------------------------------------------------------------------
# Step 3: Scoring ensemble (MUSIQ + NIMA + CLIP-IQA+ + LAION-Aes + sharpness + exposure + face)
# ---------------------------------------------------------------------------
def _laplacian_var(img: Image.Image) -> float:
    """Normalized sharpness via Laplacian variance of luminance (center crop)."""
    import torchvision.transforms.functional as TF
    w, h = img.size
    s = min(w, h)
    left = (w - s) // 2
    top = (h - s) // 2
    crop = img.crop((left, top, left + s, top + s)).resize((384, 384), Image.BILINEAR).convert("L")
    arr = torch.from_numpy(np.array(crop, dtype=np.float32) / 255.0)
    k = torch.tensor([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=torch.float32).view(1, 1, 3, 3)
    lap = torch.nn.functional.conv2d(arr.view(1, 1, 384, 384), k, padding=1)
    return float(lap.var().item())


def _exposure_penalty(img: Image.Image) -> float:
    """Return penalty in [0,1]: how over/underexposed. 0 = well exposed."""
    arr = np.asarray(img.convert("L"), dtype=np.float32) / 255.0
    mean = arr.mean()
    clipped_lo = float((arr < 0.02).mean())
    clipped_hi = float((arr > 0.98).mean())
    deviation = abs(mean - 0.5) * 2.0  # 0 @ gray, 1 @ pure black/white
    return min(1.0, 0.4 * deviation + 3.0 * clipped_lo + 3.0 * clipped_hi)


def _load_face_detector():
    try:
        import cv2
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        detector = cv2.CascadeClassifier(cascade_path)
        if detector.empty():
            print("OpenCV haarcascade missing — face bonus disabled")
            return None
        return detector
    except Exception as exc:
        print(f"Face detector unavailable ({exc}) — face bonus disabled")
        return None


def _face_bonus(img: Image.Image, detector) -> float:
    """Bonus in [0,1]: sharpness on largest detected face region (OpenCV Haar)."""
    if detector is None:
        return 0.0
    try:
        import cv2
        gray = np.asarray(img.convert("L"))
        h, w = gray.shape
        # Downsize for speed; face detection doesn't need full res
        scale = 1.0
        max_side = 1024
        if max(h, w) > max_side:
            scale = max_side / max(h, w)
            gray = cv2.resize(gray, (int(w * scale), int(h * scale)))
        faces = detector.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(40, 40))
        if len(faces) == 0:
            return 0.0
        # Largest face
        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        inv = 1.0 / scale
        x1 = int(fx * inv); y1 = int(fy * inv)
        x2 = int((fx + fw) * inv); y2 = int((fy + fh) * inv)
        area_frac = (fw * fh) / float(gray.shape[0] * gray.shape[1])
        if area_frac < 0.002:
            return 0.0
        face_crop = img.crop((x1, y1, x2, y2))
        sharp = _laplacian_var(face_crop)
        return float(1.0 - np.exp(-sharp * 50.0))
    except Exception:
        return 0.0


def _zscore_norm(values: list[float]) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float32)
    mu, sigma = arr.mean(), arr.std()
    if sigma < 1e-6:
        return np.zeros_like(arr)
    z = (arr - mu) / sigma
    # Map to [0,1] via sigmoid-ish
    return 1.0 / (1.0 + np.exp(-z))


def _compute_scores_from_components(components: dict) -> list[float]:
    """Recompute weighted ensemble from raw components (config-invariant cache)."""
    n = len(next(iter(components.values())))
    norm = {k: _zscore_norm(list(v)) for k, v in components.items()}
    combined = np.zeros(n, dtype=np.float32)
    total_w = 0.0
    for key, w in SCORE_WEIGHTS.items():
        if key in norm:
            combined += w * norm[key]
            total_w += w
    if total_w > 0:
        combined /= total_w
    if "exposure_penalty" in norm:
        combined -= EXPOSURE_PENALTY_WEIGHT * norm["exposure_penalty"]
    if "face_bonus" in norm:
        combined += FACE_BONUS_WEIGHT * norm["face_bonus"]
    return combined.tolist()


def _emit_thumbs(thumb_dir: Path, path: str, img) -> None:
    """Write the webapp's grid and compare thumbnails for one scored photo.

    Uses thumbs.py so the cache key and the resize/quality choices are the ones
    the server will look for; a mismatch here would not fail anything, it would
    just silently stop being a cache hit.

    _load_img has already applied its own >4K downscale, which is well above
    the widest thumbnail, and skipped exif_transpose because scoring does not
    care about orientation. thumbs.encode applies it, so a portrait frame is
    not cached sideways.
    """
    if img is None:
        return
    try:
        src = Path(path)
        st = src.stat()
        for w in (thumbs.GRID_MAX_WIDTH, thumbs.COMPARE_WIDTH):
            dest = thumbs.cache_file(thumb_dir, src, w, st=st)
            if not dest.exists():
                thumbs.write(dest, thumbs.encode(img, w))
    except Exception as exc:
        warnings.warn(f"Thumbnail failed {path}: {exc}")  # the webapp renders it on demand


SCORE_EXTRA_KEYS = ["sharpness", "exposure_penalty", "face_bonus"]


def _load_score_models(device: str = DEVICE) -> tuple[dict, object]:
    """The IQA metrics that loaded, and the face detector (or None)."""
    import pyiqa

    metrics: dict[str, object] = {}
    for name in ["musiq", "nima", "clipiqa+", "laion_aes"]:
        try:
            metrics[name] = pyiqa.create_metric(name, device=device)
        except Exception as exc:
            warnings.warn(f"Metric {name} unavailable ({exc}) — skipping")
    return metrics, _load_face_detector()


def _score_with_models(
    paths: list[str],
    metrics: dict,
    detector,
    device: str = DEVICE,
    thumb_dir: Path | None = None,
    desc: str | None = None,
) -> dict[str, np.ndarray]:
    """Batch-score images with loaded models. Returns dict of raw component arrays.

    Writes the webapp's thumbnails as a side effect when thumb_dir is given:
    scoring already decodes every photo off the NAS, which is the expensive
    part, so the derivatives cost 147ms per photo on top of the 717ms already
    being spent. Rendering them later from the webapp instead costs ~1310ms
    per photo, and costs it while someone is waiting to look at them.
    """
    import torchvision.transforms.functional as TF

    n = len(paths)
    raw: dict[str, list[float]] = {k: [0.0] * n for k in list(metrics.keys()) + SCORE_EXTRA_KEYS}

    def _load_img(p: str):
        try:
            img = Image.open(p).convert("RGB")
            img.load()
            w, h = img.size
            if w * h > 3840 * 2160:
                scale = ((3840 * 2160) / (w * h)) ** 0.5
                img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
            return img
        except Exception as exc:
            warnings.warn(f"Open failed {p}: {exc}")
            return None

    for batch_start in tqdm(range(0, n, SCORE_BATCH_SIZE), desc=desc or f"Scoring images ({n} total)"):
        batch_end = min(batch_start + SCORE_BATCH_SIZE, n)
        imgs = [_load_img(paths[i]) for i in range(batch_start, batch_end)]

        if thumb_dir is not None:
            for i, img in zip(range(batch_start, batch_end), imgs):
                _emit_thumbs(thumb_dir, paths[i], img)

        # Neural metrics: resize to SCORE_RESIZE for uniform batching
        tensors = [
            TF.to_tensor(img.resize((SCORE_RESIZE, SCORE_RESIZE), Image.BILINEAR))
            if img is not None else torch.zeros(3, SCORE_RESIZE, SCORE_RESIZE)
            for img in imgs
        ]
        batch_tensor = torch.stack(tensors).to(device)

        for name, metric in metrics.items():
            try:
                with torch.no_grad():
                    scores = metric(batch_tensor).reshape(-1).tolist()
                for i, s in enumerate(scores):
                    raw[name][batch_start + i] = float(s)
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                warnings.warn(f"{name} batch failed ({exc}) — retrying per-image")
                if device == "cuda":
                    torch.cuda.empty_cache()
                for i, img in enumerate(imgs):
                    if img is None:
                        continue
                    try:
                        t = TF.to_tensor(
                            img.resize((SCORE_RESIZE, SCORE_RESIZE), Image.BILINEAR)
                        ).unsqueeze(0).to(device)
                        with torch.no_grad():
                            raw[name][batch_start + i] = float(metric(t).item())
                    except Exception:
                        pass

        del batch_tensor
        if device == "cuda":
            torch.cuda.empty_cache()

        for i, img in enumerate(imgs):
            if img is None:
                continue
            raw["sharpness"][batch_start + i] = _laplacian_var(img)
            raw["exposure_penalty"][batch_start + i] = _exposure_penalty(img)
            raw["face_bonus"][batch_start + i] = _face_bonus(img, detector)

    return {k: np.asarray(v, dtype=np.float32) for k, v in raw.items()}


def score_images(
    paths: list[str],
    cache_path: Path,
    device: str = DEVICE,
    thumb_dir: Path | None = None,
    timestamps: list[float | None] | None = None,
) -> tuple[list[float], dict]:
    """Compute or load cached scores.

    Incremental: only photos missing from the cache are scored, one shooting
    day at a time, and the cache is written after every day. The cache stores
    raw components; scores are recomputed on load so SCORE_WEIGHTS changes
    invalidate nothing.
    """
    def _load(f):
        data = np.load(str(f))
        return {k: data[k] for k in data.files}

    cached = _load_path_cache(cache_path, paths, _load)
    have_all = cached is not None and all(p in next(iter(cached.values()), {}) for p in paths)
    if have_all:
        print(f"Loading cached scores ({len(paths)} paths)")
        components = {k: np.array([v[p] for p in paths], dtype=np.float32) for k, v in cached.items()}
        return _compute_scores_from_components(components), components

    metrics, detector = _load_score_models(device)
    keys = list(metrics.keys()) + SCORE_EXTRA_KEYS
    if cached is not None and set(cached) != set(keys):
        cached = None  # metric added/removed → full recompute
    done: dict[str, dict[str, float]] = cached or {k: {} for k in keys}
    todo = [i for i, p in enumerate(paths) if p not in done[keys[0]]]
    if cached:
        print(f"Incremental scoring: {len(paths) - len(todo)} cached + {len(todo)} new")

    chunks = _day_chunks(
        [paths[i] for i in todo],
        [timestamps[i] for i in todo] if timestamps is not None else None,
    )
    try:
        for day, chunk in chunks:
            new = _score_with_models(
                chunk, metrics, detector, device, thumb_dir,
                desc=f"Scoring {day} ({len(chunk)} images)",
            )
            for k in keys:
                done[k].update(zip(chunk, new[k]))
            have = [p for p in paths if p in done[keys[0]]]
            _write_path_cache(
                cache_path, have,
                lambda f: np.savez(f, **{
                    k: np.array([done[k][p] for p in have], dtype=np.float32) for k in keys
                }),
            )
    finally:
        del metrics, detector
        _release_gpu()

    print(f"Saved scores to {cache_path}  ({len(paths)} total, {len(chunks)} day checkpoint(s))")
    components = {k: np.array([done[k][p] for p in paths], dtype=np.float32) for k in keys}
    return _compute_scores_from_components(components), components


# ---------------------------------------------------------------------------
# Step 4: Rank & save — score + centroid tie-break
# ---------------------------------------------------------------------------
def rank_and_save(
    paths: list[str],
    clusters: dict[int, list[int]],
    scores: list[float],
    embeddings: np.ndarray,
    output_dir: Path,
    timestamps: list[float | None] | None = None,
    components: dict | None = None,
    motions: dict[str, str] | None = None,
    models: list[str | None] | None = None,
) -> dict:
    results_clusters = []
    for cid in sorted(clusters.keys()):
        indices = clusters[cid]
        centroid = embeddings[indices].mean(axis=0)
        centroid /= max(np.linalg.norm(centroid), 1e-8)
        centrality = embeddings[indices] @ centroid

        ranked = sorted(
            range(len(indices)),
            key=lambda j: (scores[indices[j]], float(centrality[j])),
            reverse=True,
        )
        image_entries = []
        for rank, j in enumerate(ranked, start=1):
            idx = indices[j]
            entry: dict = {
                "path": paths[idx],
                "score": round(float(scores[idx]), 4),
                "centrality": round(float(centrality[j]), 4),
                "rank": rank,
            }
            if timestamps is not None and timestamps[idx] is not None:
                entry["exif_timestamp"] = timestamps[idx]
            # The Live Photo motion file, so the review views can badge the
            # still and play it. Absent for every photo without one.
            if motions and paths[idx] in motions:
                entry["motion"] = motions[paths[idx]]
            if models and models[idx]:
                entry["model"] = models[idx]
            if components is not None:
                entry["score_components"] = {
                    k: round(float(v[idx]), 4)
                    for k, v in components.items()
                    if k not in ("exposure_penalty", "face_bonus")
                }
            image_entries.append(entry)
        best_score = scores[indices[ranked[0]]]
        cluster_ts = None
        if timestamps is not None:
            shot_at = [timestamps[i] for i in indices if timestamps[i] is not None]
            cluster_ts = min(shot_at) if shot_at else None
        entry_cluster: dict = {
            "cluster_id": int(cid),
            "cluster_score": round(float(best_score), 4),
            "best_image": paths[indices[ranked[0]]],
            "images": image_entries,
        }
        if cluster_ts is not None:
            entry_cluster["cluster_timestamp"] = cluster_ts
        results_clusters.append(entry_cluster)

    # Chronological by first shot. Clusters with no EXIF have no place on the
    # timeline, so they trail the rest ordered by score.
    results_clusters.sort(
        key=lambda c: (
            c.get("cluster_timestamp") is None,
            c.get("cluster_timestamp") or 0.0,
            -c["cluster_score"],
        )
    )

    results = {"schema_version": 1, "clusters": results_clusters}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (output_dir / "clusters.json").write_text(json.dumps({str(k): v for k, v in clusters.items()}, indent=2) + "\n")
    print(f"Wrote results.json — {len(results_clusters)} clusters")
    return results


# ---------------------------------------------------------------------------
# Step 5: Video highlights (clipfarm suggest_clips over DINOv3 frame embeddings)
# ---------------------------------------------------------------------------
def _scan_video_paths(image_dir: str) -> list[str]:
    root = Path(image_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Image directory not found: {root}")
    root_resolved = root.resolve()
    paths = []
    for dirpath, names in media.walk_media(root):
        # A Live Photo's motion file belongs to its still, not to video review.
        motion = media.motion_names(dirpath, names)
        for name in names:
            if not media.is_video(name) or name in motion:
                continue
            p = Path(dirpath, name)
            if not p.is_file():
                continue
            rp = p.resolve()
            # Skip <root>/clips/ — user-exported cuts (webapp /api/clips/export),
            # not source footage. Only the top-level clips dir; a nested sub/clips/
            # is treated as real footage.
            try:
                if rp.relative_to(root_resolved).parts[:1] == ("clips",):
                    continue
            except ValueError:
                pass
            paths.append(str(rp))
    return sorted(paths)


def _video_fingerprint(path: Path) -> str:
    st = path.stat()
    return f"{st.st_size}:{st.st_mtime_ns}"


def _make_video_frame_embedder(
    device: str = DEVICE,
    model_name: str = MODEL_NAME,
    batch_size: int = BATCH_SIZE,
):
    """Return (embed_frames, release). DINOv3 is loaded lazily on first call
    (i.e. only when some video actually needs frames embedded) and reused
    across all videos; release() frees it."""
    state: dict = {}

    def embed_frames(frame_paths) -> np.ndarray:
        if "model" not in state:
            print(f"Loading {model_name} for video frame embeddings")
            state["model"], state["processor"], state["num_skip"] = _load_embedding_model(
                model_name, device
            )
        paths = [str(p) for p in frame_paths]
        return _embed_with_model(
            paths, state["model"], state["processor"], state["num_skip"],
            device=device, batch_size=batch_size, num_workers=NUM_WORKERS,
            flip_tta=False, desc=f"DINOv3 frame embeddings ({len(paths)} frames)",
        )

    def release() -> None:
        state.pop("model", None)
        state.pop("processor", None)
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    return embed_frames, release


def compute_video_highlights(image_dir: str, output_dir: Path, force: bool = False) -> None:
    """Compute suggested highlight clips for each video under image_dir.

    Writes output_dir/video_highlights.json:
        {"schema_version": 1, "videos": {"<abs path>": {
            "fingerprint": "<size>:<mtime_ns>", "duration": 34.5, "clips": [...]}}}

    Incremental: videos whose size:mtime_ns fingerprint is unchanged are skipped
    (unless force). Entries for videos no longer on disk are dropped. The JSON is
    rewritten atomically after each video so an interrupted run keeps progress.
    Requires clipfarm; if not installed the step is skipped.
    """
    videos = _scan_video_paths(image_dir)

    try:
        from clipfarm.lib import suggest_clips
    except ImportError:
        print("clipfarm not installed — skipping video highlights (pip install -e ~/Projects/clipfarm)")
        return

    highlights_path = output_dir / "video_highlights.json"
    entries: dict[str, dict] = {}
    if highlights_path.exists():
        try:
            loaded = json.loads(highlights_path.read_text())
            if isinstance(loaded.get("videos"), dict):
                entries = loaded["videos"]
        except Exception as exc:
            warnings.warn(f"Corrupt {highlights_path.name} ({exc}) — recomputing")

    # Drop entries for videos no longer on disk
    video_set = set(videos)
    entries = {k: v for k, v in entries.items() if k in video_set}

    def _write() -> None:
        output_dir.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": 1, "videos": entries}
        tmp = highlights_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n")
        tmp.replace(highlights_path)

    if not videos:
        _write()
        print("No videos found — video_highlights.json cleared")
        return

    workdir = output_dir / "video_highlights_cache"
    embed_frames, release = _make_video_frame_embedder()
    processed = skipped = failed = 0
    try:
        for video in videos:
            try:
                fingerprint = _video_fingerprint(Path(video))
            except OSError as exc:
                warnings.warn(f"Cannot stat {video} ({exc}) — skipping")
                entries.pop(video, None)
                failed += 1
                continue
            existing = entries.get(video)
            if not force and existing and existing.get("fingerprint") == fingerprint:
                skipped += 1
                continue
            print(f"Video highlights: {video}")
            try:
                result = suggest_clips(
                    Path(video),
                    workdir=workdir,
                    embed_frames=embed_frames,
                    force=force,
                )
                entries[video] = {
                    "fingerprint": fingerprint,
                    "duration": float(result["duration"]),
                    "clips": result["clips"],
                }
                processed += 1
            except Exception as exc:
                warnings.warn(f"Video highlights failed for {video}: {exc}")
                entries.pop(video, None)
                failed += 1
            _write()
    finally:
        release()

    _write()
    print(
        f"Video highlights: {processed} computed, {skipped} cached, {failed} failed "
        f"({len(videos)} videos) → {highlights_path}"
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_pipeline(
    image_dir: str,
    output_dir: str,
    batch_size: int = BATCH_SIZE,
    tight: float = TIGHT_THRESHOLD,
    loose: float = LOOSE_THRESHOLD,
    auto_loose: bool = False,
    flip_tta: bool = False,
    max_gap_s: float = MAX_CLUSTER_GAP_S,
    orient_window_s: float = ORIENT_MERGE_WINDOW_S,
    orient_threshold: float = ORIENT_MERGE_THRESHOLD,
    cross_window_s: float = CROSS_DEVICE_WINDOW_S,
    cross_threshold: float = CROSS_DEVICE_THRESHOLD,
    video_highlights: bool = False,
    force_video_highlights: bool = False,
) -> dict:
    out = Path(output_dir)
    emb_cache = out / "embeddings_dinov3_mpcls_tta.npy"
    score_cache = out / "scores_ensemble.npz"

    paths, timestamps, orientations, models = load_paths_and_meta(image_dir)

    embeddings = compute_embeddings(
        paths,
        cache_path=emb_cache,
        batch_size=batch_size,
        flip_tta=flip_tta,
        timestamps=timestamps,
    )

    clusters = cluster_embeddings(
        embeddings,
        timestamps,
        orientations,
        tight=tight,
        loose=loose,
        max_gap_s=max_gap_s,
        orient_window_s=orient_window_s,
        orient_threshold=orient_threshold,
        auto_loose=auto_loose,
        models=models,
        cross_window_s=cross_window_s,
        cross_threshold=cross_threshold,
    )

    scores, components = score_images(
        paths, cache_path=score_cache, thumb_dir=out, timestamps=timestamps,
    )
    motions = media.motion_map(paths)
    if motions:
        print(f"Paired {len(motions)} Live Photo motion file(s) with their stills")
    results = rank_and_save(
        paths, clusters, scores, embeddings, out,
        timestamps=timestamps, components=components, motions=motions, models=models,
    )

    if video_highlights:
        compute_video_highlights(image_dir, out, force=force_video_highlights)

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Photo clustering & scoring pipeline")
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--output-dir", default=None,
                        help="Defaults to the per-project data dir the webapp reads "
                             "(~/.local/share/sightread/projects/<md5>)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--tight", type=float, default=TIGHT_THRESHOLD,
                        help="Near-duplicate cosine-dist threshold")
    parser.add_argument("--loose", type=float, default=LOOSE_THRESHOLD,
                        help="Same-scene cosine-dist threshold")
    parser.add_argument("--auto-loose", action="store_true",
                        help="Auto-calibrate loose threshold from NN distance histogram")
    parser.add_argument("--no-flip-tta", action="store_true")
    parser.add_argument("--max-gap-s", type=float, default=MAX_CLUSTER_GAP_S,
                        help="Max EXIF seconds between images in same cluster (0 to disable)")
    parser.add_argument("--orient-window-s", type=float, default=ORIENT_MERGE_WINDOW_S,
                        help="Max EXIF seconds apart to merge a portrait/landscape "
                             "re-shoot of the same subject (0 to disable)")
    parser.add_argument("--orient-threshold", type=float, default=ORIENT_MERGE_THRESHOLD,
                        help="Centroid cosine-dist ceiling for that merge")
    parser.add_argument("--cross-window-s", type=float, default=CROSS_DEVICE_WINDOW_S,
                        help="Max EXIF seconds apart to merge the same subject shot on "
                             "two different cameras (0 to disable)")
    parser.add_argument("--cross-threshold", type=float, default=CROSS_DEVICE_THRESHOLD,
                        help="Centroid cosine-dist ceiling for a same-framing "
                             "cross-camera merge")
    # Off by default since 2026-09-10. The clipfarm 2D clip-suggestion step is
    # the most expensive thing in the pipeline per unit of value: it decodes and
    # DINOv3-embeds frames across every video in the folder, and its output has
    # been accumulating a `video_highlights_cache` per project for suggestions
    # nobody is acting on. `--no-video-highlights` is kept because scripts and
    # muscle memory still pass it, and it still means what it says.
    parser.add_argument("--video-highlights", action="store_true",
                        help="Run the clipfarm video-highlights step (off by default)")
    parser.add_argument("--no-video-highlights", action="store_true",
                        help="Skip the clipfarm video-highlights step (the default; "
                             "kept so existing invocations stay valid)")
    parser.add_argument("--force-video-highlights", action="store_true",
                        help="Recompute video highlights even for unchanged videos "
                             "(implies --video-highlights)")
    args = parser.parse_args()

    if args.output_dir is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "webapp"))
        from projects import project_output_dir
        args.output_dir = str(project_output_dir(Path(args.image_dir)))
        print(f"Output dir: {args.output_dir}")

    import random
    random.seed(42)
    np.random.seed(42)

    run_pipeline(
        args.image_dir,
        args.output_dir,
        batch_size=args.batch_size,
        tight=args.tight,
        loose=args.loose,
        auto_loose=args.auto_loose,
        flip_tta=not args.no_flip_tta,
        max_gap_s=args.max_gap_s,
        orient_window_s=args.orient_window_s,
        orient_threshold=args.orient_threshold,
        cross_window_s=args.cross_window_s,
        cross_threshold=args.cross_threshold,
        video_highlights=((args.video_highlights or args.force_video_highlights)
                          and not args.no_video_highlights),
        force_video_highlights=args.force_video_highlights,
    )
    print("Pipeline complete")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
DINOv3 + two-stage clustering + IQA ensemble photo curation pipeline.

Usage:
    python scripts/pipeline.py --image-dir /path/to/photos
    python scripts/pipeline.py --image-dir /path/to/photos --output-dir outputs
"""

import argparse
import gc
import json
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ExifTags
from tqdm import tqdm

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

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}
# FAISS k-NN connectivity replaces O(n²) sklearn distance matrix above this size
_FAISS_N_THRESHOLD = 5_000
_FAISS_K_NEIGHBORS = 50     # neighbors per point for connectivity graph
# Scoring batch size and resize for uniform GPU batching
SCORE_BATCH_SIZE = 16
SCORE_RESIZE = 512           # resize to this before neural metrics
_EXIF_DATETIME_TAG = next(k for k, v in ExifTags.TAGS.items() if v == "DateTimeOriginal")


# ---------------------------------------------------------------------------
# Scanning
# ---------------------------------------------------------------------------
def _scan_image_paths(image_dir: str) -> list[str]:
    root = Path(image_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Image directory not found: {root}")
    return sorted(
        str(p) for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _read_exif_timestamp(path: str) -> float | None:
    """Return EXIF DateTimeOriginal as unix seconds, or None."""
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
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
            dt = datetime.strptime(raw, "%Y:%m:%d %H:%M:%S")
            return dt.timestamp()
    except Exception:
        return None


def load_paths_and_timestamps(image_dir: str) -> tuple[list[str], list[float | None]]:
    paths = _scan_image_paths(image_dir)
    if not paths:
        raise RuntimeError(f"No images found in {image_dir}")
    timestamps = [_read_exif_timestamp(p) for p in tqdm(paths, desc="Reading EXIF")]
    print(f"Found {len(paths)} images ({sum(t is not None for t in timestamps)} with EXIF timestamps)")
    return paths, timestamps


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


def _run_embedding_model(
    paths: list[str],
    device: str = DEVICE,
    model_name: str = MODEL_NAME,
    batch_size: int = BATCH_SIZE,
    num_workers: int = NUM_WORKERS,
    flip_tta: bool = False,
) -> np.ndarray:
    """Compute L2-normalized embeddings. No caching. Returns float32 [N, 2D]."""
    from transformers import AutoImageProcessor, AutoModel

    processor = AutoImageProcessor.from_pretrained(model_name)
    model = AutoModel.from_pretrained(model_name).to(device).eval()

    num_register = int(getattr(model.config, "num_register_tokens", 0) or 0)
    num_skip = 1 + num_register

    ds = _ImageDataset(paths, processor)
    loader = torch.utils.data.DataLoader(
        ds, batch_size=batch_size, num_workers=num_workers,
        pin_memory=(device == "cuda"), shuffle=False,
    )

    all_feats: list[np.ndarray] = []
    for batch in tqdm(loader, desc=f"DINOv3 embeddings ({len(paths)} images)"):
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

    del model, processor
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return embeddings


def compute_embeddings(
    paths: list[str],
    cache_path: Path,
    device: str = DEVICE,
    model_name: str = MODEL_NAME,
    batch_size: int = BATCH_SIZE,
    num_workers: int = NUM_WORKERS,
    flip_tta: bool = False,
) -> np.ndarray:
    """Compute or load cached embeddings. Incremental: only new paths are processed."""
    paths_sidecar = cache_path.with_suffix(".paths.json")

    if cache_path.exists() and paths_sidecar.exists():
        cached_paths = json.loads(paths_sidecar.read_text())
        cached_set = set(cached_paths)
        new_paths = [p for p in paths if p not in cached_set]

        if cached_set <= set(paths):
            old_embs = np.load(str(cache_path))
            old_idx = {p: i for i, p in enumerate(cached_paths)}

            if not new_paths:
                print(f"Loading cached embeddings ({len(paths)} paths)")
                return np.array([old_embs[old_idx[p]] for p in paths], dtype=np.float32)

            print(f"Incremental embeddings: {len(cached_paths)} cached + {len(new_paths)} new")
            new_embs = _run_embedding_model(new_paths, device, model_name, batch_size, num_workers, flip_tta)
            new_idx = {p: i for i, p in enumerate(new_paths)}
            d = old_embs.shape[1]
            result = np.empty((len(paths), d), dtype=np.float32)
            for i, p in enumerate(paths):
                result[i] = old_embs[old_idx[p]] if p in old_idx else new_embs[new_idx[p]]
            np.save(str(cache_path), result)
            paths_sidecar.write_text(json.dumps(paths))
            print(f"Updated embeddings cache → {len(paths)} total")
            return result

    # Full recompute
    embeddings = _run_embedding_model(paths, device, model_name, batch_size, num_workers, flip_tta)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(cache_path), embeddings)
    paths_sidecar.write_text(json.dumps(paths))
    # Remove stale .hash sidecar from old cache format
    old_hash = cache_path.with_suffix(".hash")
    if old_hash.exists():
        old_hash.unlink()
    print(f"Saved embeddings to {cache_path}  shape={embeddings.shape}")
    return embeddings


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


def cluster_embeddings(
    embeddings: np.ndarray,
    timestamps: list[float | None],
    tight: float = TIGHT_THRESHOLD,
    loose: float = LOOSE_THRESHOLD,
    burst_window_s: float = BURST_WINDOW_S,
    max_gap_s: float = MAX_CLUSTER_GAP_S,
    auto_loose: bool = False,
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


def _run_score_model(paths: list[str], device: str = DEVICE) -> dict[str, np.ndarray]:
    """Batch-score images. Returns dict of raw component arrays."""
    import pyiqa
    import torchvision.transforms.functional as TF

    metric_names = ["musiq", "nima", "clipiqa+", "laion_aes"]
    metrics: dict[str, object] = {}
    for name in metric_names:
        try:
            metrics[name] = pyiqa.create_metric(name, device=device)
        except Exception as exc:
            warnings.warn(f"Metric {name} unavailable ({exc}) — skipping")

    detector = _load_face_detector()
    n = len(paths)
    raw: dict[str, list[float]] = {
        k: [0.0] * n for k in list(metrics.keys()) + ["sharpness", "exposure_penalty", "face_bonus"]
    }

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

    for batch_start in tqdm(range(0, n, SCORE_BATCH_SIZE), desc=f"Scoring images ({n} total)"):
        batch_end = min(batch_start + SCORE_BATCH_SIZE, n)
        imgs = [_load_img(paths[i]) for i in range(batch_start, batch_end)]

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

    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    return {k: np.asarray(v, dtype=np.float32) for k, v in raw.items()}


def score_images(
    paths: list[str],
    cache_path: Path,
    device: str = DEVICE,
) -> tuple[list[float], dict]:
    """Compute or load cached scores. Incremental: only new paths are scored.

    Cache stores raw components; scores recomputed on load so SCORE_WEIGHTS changes
    invalidate nothing.
    """
    paths_sidecar = cache_path.with_suffix(".paths.json")

    if cache_path.exists() and paths_sidecar.exists():
        cached_paths = json.loads(paths_sidecar.read_text())
        cached_set = set(cached_paths)
        new_paths = [p for p in paths if p not in cached_set]

        if cached_set <= set(paths):
            data = np.load(str(cache_path))
            old_components = {k: data[k] for k in data.files}
            old_idx = {p: i for i, p in enumerate(cached_paths)}

            if not new_paths:
                print(f"Loading cached scores ({len(paths)} paths)")
                components = {
                    k: np.array([v[old_idx[p]] for p in paths], dtype=np.float32)
                    for k, v in old_components.items()
                }
                return _compute_scores_from_components(components), components

            print(f"Incremental scoring: {len(cached_paths)} cached + {len(new_paths)} new")
            new_components = _run_score_model(new_paths, device)

            if set(new_components.keys()) == set(old_components.keys()):
                new_idx = {p: i for i, p in enumerate(new_paths)}
                merged = {}
                for k in old_components:
                    arr = np.empty(len(paths), dtype=np.float32)
                    for i, p in enumerate(paths):
                        arr[i] = old_components[k][old_idx[p]] if p in old_idx else new_components[k][new_idx[p]]
                    merged[k] = arr
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                np.savez(str(cache_path), **merged)
                paths_sidecar.write_text(json.dumps(paths))
                print(f"Updated scores cache → {len(paths)} total")
                return _compute_scores_from_components(merged), merged
            # Keys differ (metric added/removed) → fall through to full recompute

    # Full recompute (also handles legacy cache without paths sidecar)
    components = _run_score_model(paths, device)
    scores = _compute_scores_from_components(components)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(str(cache_path), **components)
    paths_sidecar.write_text(json.dumps(paths))
    print(f"Saved scores to {cache_path}")
    return scores, components


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
            if components is not None:
                entry["score_components"] = {
                    k: round(float(v[idx]), 4)
                    for k, v in components.items()
                    if k not in ("exposure_penalty", "face_bonus")
                }
            image_entries.append(entry)
        best_score = scores[indices[ranked[0]]]
        results_clusters.append({
            "cluster_id": int(cid),
            "cluster_score": round(float(best_score), 4),
            "best_image": paths[indices[ranked[0]]],
            "images": image_entries,
        })

    results_clusters.sort(key=lambda c: c["cluster_score"], reverse=True)

    results = {"schema_version": 1, "clusters": results_clusters}
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    (output_dir / "clusters.json").write_text(json.dumps({str(k): v for k, v in clusters.items()}, indent=2) + "\n")
    print(f"Wrote results.json — {len(results_clusters)} clusters")
    return results


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_pipeline(
    image_dir: str,
    output_dir: str = "outputs",
    batch_size: int = BATCH_SIZE,
    tight: float = TIGHT_THRESHOLD,
    loose: float = LOOSE_THRESHOLD,
    auto_loose: bool = False,
    flip_tta: bool = False,
    max_gap_s: float = MAX_CLUSTER_GAP_S,
) -> dict:
    out = Path(output_dir)
    emb_cache = out / "embeddings_dinov3_mpcls_tta.npy"
    score_cache = out / "scores_ensemble.npz"

    paths, timestamps = load_paths_and_timestamps(image_dir)

    embeddings = compute_embeddings(
        paths,
        cache_path=emb_cache,
        batch_size=batch_size,
        flip_tta=flip_tta,
    )

    clusters = cluster_embeddings(
        embeddings,
        timestamps,
        tight=tight,
        loose=loose,
        max_gap_s=max_gap_s,
        auto_loose=auto_loose,
    )

    scores, components = score_images(paths, cache_path=score_cache)
    return rank_and_save(paths, clusters, scores, embeddings, out, timestamps=timestamps, components=components)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Photo clustering & scoring pipeline")
    parser.add_argument("--image-dir", required=True)
    parser.add_argument("--output-dir", default="outputs")
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
    args = parser.parse_args()

    run_pipeline(
        args.image_dir,
        args.output_dir,
        batch_size=args.batch_size,
        tight=args.tight,
        loose=args.loose,
        auto_loose=args.auto_loose,
        flip_tta=not args.no_flip_tta,
        max_gap_s=args.max_gap_s,
    )
    print("Pipeline complete")


if __name__ == "__main__":
    main()

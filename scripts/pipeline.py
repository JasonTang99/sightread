#!/usr/bin/env python3
"""
CLIP + HDBSCAN + pyiqa photo clustering and scoring pipeline.

Usage:
    python scripts/pipeline.py --image-dir /path/to/photos
    python scripts/pipeline.py --image-dir /path/to/photos --output-dir outputs
"""

import argparse
import json
import warnings
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------
DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MODEL_NAME = "ViT-B-16"
PRETRAINED = "openai"
BATCH_SIZE = 32
CLUSTER_MIN_SIZE = 3
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}


# ---------------------------------------------------------------------------
# Step 1: Load images
# ---------------------------------------------------------------------------
def load_images(image_dir: str) -> tuple[list[str], list[Image.Image]]:
    """Recursively scan *image_dir* and return (paths, PIL images).

    Corrupted files are skipped with a warning.
    """
    root = Path(image_dir)
    if not root.is_dir():
        raise FileNotFoundError(f"Image directory not found: {root}")

    paths: list[str] = []
    images: list[Image.Image] = []

    candidates = sorted(
        p for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS
    )
    for p in tqdm(candidates, desc="Loading images"):
        try:
            img = Image.open(p).convert("RGB")
            # Force load so corrupt files fail here
            img.load()
            paths.append(str(p))
            images.append(img)
        except Exception as exc:
            warnings.warn(f"Skipping {p}: {exc}")

    print(f"Loaded {len(images)} images from {root}")
    return paths, images


# ---------------------------------------------------------------------------
# Step 2: Compute CLIP embeddings
# ---------------------------------------------------------------------------
def compute_embeddings(
    images: list[Image.Image],
    cache_path: Path,
    device: str = DEVICE,
    model_name: str = MODEL_NAME,
    batch_size: int | None = None,
) -> np.ndarray:
    """Return L2-normalised CLIP embeddings, using cache when available."""
    if batch_size is None:
        batch_size = BATCH_SIZE

    if cache_path.exists():
        print(f"Loading cached embeddings from {cache_path}")
        return np.load(str(cache_path))

    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms(
        model_name, pretrained=PRETRAINED, device=device
    )
    model.eval()

    all_embeddings: list[np.ndarray] = []
    for start in tqdm(range(0, len(images), batch_size), desc="Computing CLIP embeddings"):
        batch_imgs = images[start : start + batch_size]
        batch_tensors = torch.stack([preprocess(img) for img in batch_imgs]).to(device)

        with torch.no_grad(), torch.amp.autocast(device_type=device if device != "cpu" else "cpu"):
            feats = model.encode_image(batch_tensors)

        feats = feats.cpu().numpy()
        all_embeddings.append(feats)

    embeddings = np.concatenate(all_embeddings, axis=0).astype(np.float32)
    # L2 normalise
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    norms[norms == 0] = 1
    embeddings = embeddings / norms

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(str(cache_path), embeddings)
    print(f"Saved embeddings to {cache_path}  shape={embeddings.shape}")

    # Free GPU memory before scoring step
    del model, preprocess
    import gc
    gc.collect()
    torch.cuda.empty_cache()

    return embeddings


# ---------------------------------------------------------------------------
# Step 3: Clustering (HDBSCAN)
# ---------------------------------------------------------------------------
def cluster_embeddings(
    embeddings: np.ndarray,
    min_cluster_size: int | None = None,
) -> dict[int, list[int]]:
    """Cluster embeddings with HDBSCAN. Noise points become singleton clusters."""
    if min_cluster_size is None:
        min_cluster_size = CLUSTER_MIN_SIZE

    import hdbscan

    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        metric="euclidean",
    )
    labels = clusterer.fit_predict(embeddings)

    clusters: dict[int, list[int]] = {}
    next_id = int(labels.max()) + 1 if labels.max() >= 0 else 0

    for idx, label in enumerate(labels):
        if label == -1:
            # Noise → singleton cluster
            clusters[next_id] = [idx]
            next_id += 1
        else:
            clusters.setdefault(int(label), []).append(idx)

    print(f"Found {len(clusters)} clusters (including singletons)")
    return clusters


# ---------------------------------------------------------------------------
# Step 4: Aesthetic / quality scoring
# ---------------------------------------------------------------------------
def score_images(
    paths: list[str],
    device: str = DEVICE,
    max_pixels: int = 1920 * 1080,
) -> list[float]:
    """Score every image using pyiqa topiq_nr (no-reference quality).

    Large images are resized to fit within *max_pixels* to avoid OOM.
    """
    import gc
    import pyiqa

    # Aggressively free GPU memory from prior steps
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    # Try GPU first, fall back to CPU on OOM
    try:
        metric = pyiqa.create_metric("topiq_nr", device=device)
        _score_one(metric, paths[0], max_pixels)
    except (torch.cuda.OutOfMemoryError, RuntimeError):
        print("GPU OOM during scoring — falling back to CPU")
        del metric
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        device = "cpu"
        metric = pyiqa.create_metric("topiq_nr", device=device)

    scores: list[float] = []
    for p in tqdm(paths, desc="Scoring images"):
        try:
            score = _score_one(metric, p, max_pixels)
        except Exception as exc:
            warnings.warn(f"Scoring failed for {p}: {exc}")
            score = 0.0
        scores.append(score)
    return scores


def _score_one(metric, path: str, max_pixels: int) -> float:
    """Score a single image, resizing if needed to stay within memory budget."""
    import torchvision.transforms.functional as TF
    img = Image.open(path).convert("RGB")
    w, h = img.size

    # Resize large images to keep total pixels under max_pixels
    if w * h > max_pixels:
        scale = (max_pixels / (w * h)) ** 0.5
        new_w, new_h = int(w * scale), int(h * scale)
        img = img.resize((new_w, new_h), Image.LANCZOS)

    tensor = TF.to_tensor(img).unsqueeze(0)
    if next(metric.parameters(), None) is not None:
        tensor = tensor.to(next(metric.parameters()).device)
    with torch.no_grad():
        score = metric(tensor).item()
    return score


# ---------------------------------------------------------------------------
# Step 5 + 6: Rank & save
# ---------------------------------------------------------------------------
def rank_and_save(
    paths: list[str],
    clusters: dict[int, list[int]],
    scores: list[float],
    output_dir: Path,
) -> dict:
    """Rank within each cluster and write results.json + clusters.json."""
    results_clusters = []
    for cid in sorted(clusters.keys()):
        indices = clusters[cid]
        # Sort by score descending
        ranked = sorted(indices, key=lambda i: scores[i], reverse=True)
        image_entries = []
        for rank, idx in enumerate(ranked, start=1):
            image_entries.append({
                "path": paths[idx],
                "score": round(scores[idx], 4),
                "rank": rank,
            })
        results_clusters.append({
            "cluster_id": cid,
            "best_image": paths[ranked[0]],
            "images": image_entries,
        })

    results = {"clusters": results_clusters}
    output_dir.mkdir(parents=True, exist_ok=True)

    results_path = output_dir / "results.json"
    results_path.write_text(json.dumps(results, indent=2) + "\n")
    print(f"Wrote {results_path} — {len(results_clusters)} clusters")

    clusters_path = output_dir / "clusters.json"
    clusters_path.write_text(json.dumps(clusters, indent=2) + "\n")
    print(f"Wrote {clusters_path}")

    return results


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run_pipeline(
    image_dir: str,
    output_dir: str = "outputs",
    batch_size: int | None = None,
    min_cluster_size: int | None = None,
) -> dict:
    """Run the full pipeline: load → embed → cluster → score → rank → save."""
    import gc

    out = Path(output_dir)
    cache_path = out / "embeddings.npy"

    if cache_path.exists():
        # Only need paths, not PIL images
        paths = _scan_image_paths(image_dir)
        if not paths:
            raise RuntimeError("No valid images found")
        embeddings = compute_embeddings([], cache_path=cache_path, batch_size=batch_size)
    else:
        paths, images = load_images(image_dir)
        if not paths:
            raise RuntimeError("No valid images found")
        embeddings = compute_embeddings(images, cache_path=cache_path, batch_size=batch_size)
        del images
        gc.collect()

    clusters = cluster_embeddings(embeddings, min_cluster_size=min_cluster_size)

    # Free embeddings
    del embeddings
    gc.collect()

    scores = score_images(paths)
    results = rank_and_save(paths, clusters, scores, out)
    return results


def _scan_image_paths(image_dir: str) -> list[str]:
    """Return sorted image paths without loading them."""
    root = Path(image_dir)
    return sorted(
        str(p) for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Photo clustering & scoring pipeline")
    parser.add_argument("--image-dir", required=True, help="Path to folder of images")
    parser.add_argument("--output-dir", default="outputs", help="Output directory (default: outputs)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--min-cluster-size", type=int, default=CLUSTER_MIN_SIZE)
    args = parser.parse_args()

    run_pipeline(
        args.image_dir,
        args.output_dir,
        batch_size=args.batch_size,
        min_cluster_size=args.min_cluster_size,
    )
    print("✅ Pipeline complete")


if __name__ == "__main__":
    main()

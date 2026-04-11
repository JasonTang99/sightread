# 📸 Photo Clustering + Scoring Pipeline (Implementation Spec)

## 🎯 Objective
Implement the core backend that:
1. Computes CLIP embeddings for all images
2. Clusters similar images (same scene / burst shots)
3. Scores images for quality/aesthetic
4. Ranks images within each cluster
5. Outputs structured JSON

---

# 📦 Dependencies

Install:

pip install torch torchvision torchaudio
pip install open_clip_torch
pip install numpy pandas tqdm pillow
pip install hdbscan scikit-learn
pip install pyiqa

---

# 📁 Input

- Folder: `images/`
- Contains JPG/PNG images

---

# 📁 Output

Create:

outputs/
├── embeddings.npy
├── clusters.json
└── results.json

---

# ⚙️ Config

Use these defaults:

DEVICE = "cuda"
MODEL_NAME = "ViT-B-16"
BATCH_SIZE = 32

CLUSTER_MIN_SIZE = 3
TOP_K = 3

---

# 🧩 Step 1: Load Images

- Recursively scan `images/`
- Load with PIL
- Convert to RGB
- Store:
  - file paths
  - PIL images

---

# 🧠 Step 2: Compute CLIP Embeddings

## Requirements

- Use `open_clip`
- Model: ViT-B-16
- Use GPU if available
- Batch inference
- Use mixed precision

## Implementation Details

- Preprocess images using CLIP transform
- Encode images → embeddings
- Convert to numpy

## Normalize embeddings (IMPORTANT)

embeddings = embeddings / np.linalg.norm(embeddings, axis=1, keepdims=True)

## Cache

Save to:

outputs/embeddings.npy

If file exists → load instead of recomputing

---

# 🔍 Step 3: Clustering (HDBSCAN)

## Requirements

- Use normalized embeddings
- Algorithm: HDBSCAN
- Metric: euclidean

## Implementation

clusterer = hdbscan.HDBSCAN(
    min_cluster_size=CLUSTER_MIN_SIZE,
    metric='euclidean'
)

labels = clusterer.fit_predict(embeddings)

## Notes

- label = -1 → noise (ignore or treat as singletons)

## Build cluster dictionary

clusters = {
    cluster_id: [indices]
}

Save:

outputs/clusters.json

---

# 🎯 Step 4: Aesthetic / Quality Scoring

## Use

pyiqa library

## Recommended model

"topiq_nr" (no-reference quality)

## Requirements

- Load model once
- Run inference per image
- Use GPU if available

## Output

scores = [float per image]

---

# 🏆 Step 5: Ranking

For each cluster:

1. Get image indices
2. Sort by score descending

sorted_cluster = sorted(
    indices,
    key=lambda i: scores[i],
    reverse=True
)

3. Assign ranks

rank = position in sorted list

4. Select top K

top_k = sorted_cluster[:TOP_K]

---

# 📤 Step 6: Save Results

## Format

results.json:

{
  "clusters": [
    {
      "cluster_id": 0,
      "best_image": "path/to/image.jpg",
      "images": [
        {
          "path": "...",
          "score": 0.91,
          "rank": 1
        }
      ]
    }
  ]
}

## Requirements

- Use original file paths
- Include ALL images in each cluster
- Rank starts at 1

---

# ⚡ Performance Requirements

- Use batching for CLIP
- Use torch.no_grad()
- Use autocast (mixed precision)
- Do NOT recompute embeddings if cached

---

# ⚠️ Edge Cases

- Corrupted images → skip with warning
- Very small clusters → still include
- Noise points (-1):
  - Either:
    - treat each as its own cluster
    OR
    - exclude (configurable)

---

# 🧪 Optional Enhancements (if time permits)

## 1. Blur detection (OpenCV)

- Use Laplacian variance
- Penalize blurry images

## 2. Deduplication

- If cosine similarity > 0.95 → mark as duplicate

## 3. Exposure check

- Penalize over/underexposed images

---

# 🚀 Entry Function

Implement:

def run_pipeline(image_dir: str):
    load images
    compute/load embeddings
    cluster embeddings
    score images
    rank clusters
    save outputs

---

# ✅ Expected Behavior

Given burst photos:

- Images of same scene grouped together
- Best photo ranked #1
- Lower quality duplicates ranked lower

---

# 🔥 Important Notes

- CLIP embeddings MUST be normalized
- HDBSCAN handles unknown cluster count automatically
- Scoring model should run AFTER clustering
- Keep pipeline modular (each step reusable)


"""
임베딩 기반 카테고리 정밀도 필터.

키워드 매칭으로 1차 candidate 추출 후, 기사-카테고리 의미적 유사도가
threshold 미만이면 컷 (false positive 제거).

- 모델: BAAI/bge-m3 (다국어, 한국어 우수, ~2.3GB, 최초 1회 다운로드)
- 정규화 임베딩의 dot product = cosine similarity
- 카테고리당 multi-vector: (label+keywords) + 각 example 문장 → max similarity
  → 추상적 라벨로 인한 false negative 줄임 (예: '중동·지정학' 0.31 → 0.55)
- 카테고리 정의 변경 시 캐시 자동 무효화 (해시 기반)
- sentence-transformers 미설치/모델 로드 실패 시 자동 비활성화 (graceful fallback)
"""

import hashlib
import json
import os
from typing import Optional

import numpy as np

MODEL_NAME = "BAAI/bge-m3"
CACHE_DIR = "/tmp"
CAT_CACHE_PATH = os.path.join(CACHE_DIR, "category_embeddings.npz")
CAT_HASH_PATH = os.path.join(CACHE_DIR, "category_embeddings.hash")
DEFAULT_THRESHOLD = 0.45

_model = None
_load_failed = False


def _get_model():
    global _model, _load_failed
    if _model is not None:
        return _model
    if _load_failed:
        return None
    try:
        from sentence_transformers import SentenceTransformer
        print(f"[category_matcher] 모델 로드 중: {MODEL_NAME} (최초 1회는 ~2.3GB 다운로드)")
        _model = SentenceTransformer(MODEL_NAME)
        return _model
    except Exception as e:
        print(f"[category_matcher] 모델 로드 실패, 임베딩 필터 비활성화: {e}")
        _load_failed = True
        return None


def _categories_hash(categories: list[dict]) -> str:
    payload = json.dumps(
        [(c["id"], c["label"], list(c.get("keywords", [])),
          list(c.get("examples", [])))
         for c in categories],
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _category_texts(c: dict) -> list[str]:
    """카테고리당 임베딩할 텍스트 리스트. (label+keywords) + 각 example."""
    kws = ", ".join(c.get("keywords", []))
    base = f"{c['label']}. 관련 키워드: {kws}"
    return [base] + list(c.get("examples", []))


def build_category_index(categories: list[dict]) -> Optional[dict[str, np.ndarray]]:
    """카테고리 → (N, D) 정규화 임베딩 행렬. 모델 사용 불가면 None."""
    cur_hash = _categories_hash(categories)
    if os.path.exists(CAT_CACHE_PATH) and os.path.exists(CAT_HASH_PATH):
        try:
            with open(CAT_HASH_PATH) as f:
                cached_hash = f.read().strip()
            if cached_hash == cur_hash:
                data = np.load(CAT_CACHE_PATH)
                return {k: data[k] for k in data.files}
        except Exception:
            pass

    model = _get_model()
    if model is None:
        return None

    out: dict[str, np.ndarray] = {}
    for c in categories:
        texts = _category_texts(c)
        embs = model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        out[c["id"]] = np.asarray(embs, dtype=np.float32)  # (N, D)

    np.savez(CAT_CACHE_PATH, **out)
    with open(CAT_HASH_PATH, "w") as f:
        f.write(cur_hash)
    return out


def score_article_categories(title: str, content: str,
                             cat_index: dict[str, np.ndarray]) -> dict[str, float]:
    """
    기사 텍스트와 각 카테고리의 multi-vector 사이 max cosine similarity.
    예시 문장 중 하나에만 가까워도 매칭으로 인정.
    """
    model = _get_model()
    if model is None or not cat_index:
        return {}
    text = (title + " " + (content or "")[:500]).strip()
    if not text:
        return {}
    emb = model.encode(text, normalize_embeddings=True, show_progress_bar=False)
    emb = np.asarray(emb, dtype=np.float32)
    return {cid: float(np.max(mat @ emb)) for cid, mat in cat_index.items()}

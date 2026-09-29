"""KURE-v1/Jina v5 기반 하이브리드 임베딩 검색.

선정 기준
---------
* 한국어 중심의 짧은 문서/질의: ``nlpai-lab/KURE-v1``
  BGE-M3를 한국어 검색 데이터로 추가 학습한 모델이라 조사·어미와 한국어 문맥
  보존을 우선할 때 사용한다.
* 영문 전문용어가 많거나 한영 혼합/장문인 문서: ``jinaai/jina-embeddings-v5-text-small``
  검색(retrieval) 용도로 설계된 다국어 모델이며 긴 기술 문서에 사용한다.
* ``strategy="hybrid"``: 두 모델의 cosine 점수를 각각 정규화한 뒤 언어 특성에
  따라 가중 합산한다. 서로 차원이 달라도 점수 단계에서 결합하므로 안전하다.

모델은 Hugging Face 공개 모델을 ``sentence-transformers`` 로 로컬 실행한다.
네트워크 다운로드는 최초 실행 때만 필요하고 이후에는 로컬 캐시를 사용한다.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, List, Literal, Optional, Protocol, Sequence

KURE_MODEL_ID = "nlpai-lab/KURE-v1"
JINA_MODEL_ID = "jinaai/jina-embeddings-v5-text-small"

ModelChoice = Literal["kure", "jina"]
Strategy = Literal["auto", "kure", "jina", "hybrid"]

_KOREAN_RE = re.compile(r"[가-힣]")
_TECHNICAL_RE = re.compile(
    r"\b(?:NPU|GPU|CXL|HBM|ASIC|SoC|TOPS|W|Foundry|Architecture|PCIe|UCIe|"
    r"LLM|RAG|inference|chiplet|photonics|SerDes|PIM|CIM)\b",
    re.IGNORECASE,
)


class EmbeddingBackend(Protocol):
    def encode(self, texts: Sequence[str]) -> List[List[float]]: ...


class EmbeddingConfigurationError(RuntimeError):
    """오픈소스 임베딩 실행 의존성이 준비되지 않은 경우."""


def korean_character_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(bool(_KOREAN_RE.match(ch)) for ch in letters) / len(letters)


def choose_embedding_model(text: str) -> ModelChoice:
    """언어와 문서 성격에 따라 단일 우선 모델을 선택한다."""
    ratio = korean_character_ratio(text)
    is_long = len(text) > 1800 or len(text.split()) > 320
    is_technical = bool(_TECHNICAL_RE.search(text))
    # 한국어 비중이 높고 짧은 일반 문맥은 KURE가 우선이다.
    if ratio >= 0.35 and not is_long and not is_technical:
        return "kure"
    # 한영 혼합 전문용어 또는 장문은 Jina가 우선이다.
    return "jina"


class SentenceTransformerBackend:
    """sentence-transformers를 지연 로드하는 Hugging Face 백엔드."""

    def __init__(self, model_id: str) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - optional runtime dependency
            raise EmbeddingConfigurationError(
                "오픈소스 임베딩 의존성이 없습니다. "
                "`uv sync --extra embeddings` 후 다시 실행하세요."
            ) from exc
        # Jina 계열은 모델 저장소의 커스텀 코드를 사용할 수 있다.
        self.model = SentenceTransformer(model_id, trust_remote_code=True)

    def encode(self, texts: Sequence[str]) -> List[List[float]]:
        vectors = self.model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True
        )
        return [vector.tolist() for vector in vectors]


class OpenSourceEmbeddingRouter:
    """KURE/Jina 모델을 필요할 때만 로드하고 텍스트별로 라우팅한다."""

    def __init__(
        self,
        *,
        kure_backend: Optional[EmbeddingBackend] = None,
        jina_backend: Optional[EmbeddingBackend] = None,
        backend_factory: Callable[[str], EmbeddingBackend] = SentenceTransformerBackend,
    ) -> None:
        self._kure = kure_backend
        self._jina = jina_backend
        self._factory = backend_factory

    def backend(self, choice: ModelChoice) -> EmbeddingBackend:
        if choice == "kure":
            if self._kure is None:
                self._kure = self._factory(KURE_MODEL_ID)
            return self._kure
        if self._jina is None:
            self._jina = self._factory(JINA_MODEL_ID)
        return self._jina

    def embed(self, texts: Sequence[str], *, model: ModelChoice) -> List[List[float]]:
        return self.backend(model).encode(texts)


@dataclass(frozen=True)
class RetrievalResult:
    index: int
    score: float
    text: str
    metadata: Any = None


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        raise ValueError("같은 차원의 비어 있지 않은 벡터가 필요합니다")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    return dot / (norm_a * norm_b) if norm_a and norm_b else 0.0


def _minmax(values: Sequence[float]) -> List[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if math.isclose(lo, hi):
        return [1.0 for _ in values]
    return [(value - lo) / (hi - lo) for value in values]


class HybridEmbeddingRetriever:
    """두 오픈소스 모델의 검색 점수를 결합하는 인메모리 retriever."""

    def __init__(self, router: Optional[OpenSourceEmbeddingRouter] = None) -> None:
        self.router = router or OpenSourceEmbeddingRouter()

    def search(
        self,
        query: str,
        documents: Sequence[str],
        *,
        metadata: Optional[Sequence[Any]] = None,
        top_k: int = 5,
        strategy: Strategy = "hybrid",
    ) -> List[RetrievalResult]:
        if not documents or top_k <= 0:
            return []
        if metadata is not None and len(metadata) != len(documents):
            raise ValueError("metadata와 documents 길이가 같아야 합니다")

        if strategy == "auto":
            strategy = choose_embedding_model(query)

        models: Iterable[ModelChoice]
        if strategy == "hybrid":
            models = ("kure", "jina")
        elif strategy in ("kure", "jina"):
            models = (strategy,)
        else:
            raise ValueError(f"알 수 없는 임베딩 전략: {strategy!r}")

        normalized_by_model: dict[str, List[float]] = {}
        for model in models:
            vectors = self.router.embed([query, *documents], model=model)
            raw = [_cosine(vectors[0], vector) for vector in vectors[1:]]
            normalized_by_model[model] = _minmax(raw)

        if strategy == "hybrid":
            # 한국어 비중에 따라 KURE 비중을 0.35~0.7 범위에서 조절한다.
            kure_weight = min(0.7, max(0.35, korean_character_ratio(query) + 0.25))
            scores = [
                kure_weight * normalized_by_model["kure"][i]
                + (1.0 - kure_weight) * normalized_by_model["jina"][i]
                for i in range(len(documents))
            ]
        else:
            scores = normalized_by_model[strategy]

        ranked = sorted(range(len(documents)), key=lambda i: (-scores[i], i))[:top_k]
        return [
            RetrievalResult(
                index=index,
                score=scores[index],
                text=documents[index],
                metadata=metadata[index] if metadata is not None else None,
            )
            for index in ranked
        ]

"""이중 FAISS 검색: 모델 공간 분리, 기업 필터, 절대 유사도 기준."""

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sys

# macOS OpenMP 충돌: torch·faiss가 각자 libomp를 포함해 인덱싱 중 세그폴트·abort 발생.
# import 순서로는 해결되지 않아 libomp 로드 전 단일 스레드로 고정 (사용자 지정 값 우선).
if sys.platform == "darwin":
    os.environ.setdefault("OMP_NUM_THREADS", "1")

import faiss
import numpy as np

from investment_scout.rag.chunking import Chunk, chunk_document
from investment_scout.rag.documents import DocumentCorpus
from investment_scout.rag.embeddings import MODEL_IDS, LocalEmbeddings
from investment_scout.rag.storage import read_json, write_json
from investment_scout.retrieval.embeddings import choose_embedding_model


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    score: float
    model_scores: dict[str, float]


def _vectors(values) -> np.ndarray:
    vectors = np.array(values, dtype="float32", order="C", copy=True)
    if vectors.ndim != 2 or not np.isfinite(vectors).all():
        raise ValueError("임베딩은 유한한 2차원 배열이어야 합니다.")
    if np.any(np.linalg.norm(vectors, axis=1) == 0):
        raise ValueError("영벡터는 검색에 사용할 수 없습니다.")
    faiss.normalize_L2(vectors)
    return vectors


class DualFaissIndex:
    def __init__(self, chunks: list[Chunk], indexes: dict, embedder=None):
        self.chunks = chunks
        self.indexes = indexes
        self.embedder = embedder or LocalEmbeddings()
        self._query_vectors = {}

    def prepare_queries(self, queries: list[str]) -> None:
        """질문 일괄 변환: CPU에서 기업마다 모델을 다시 로드하는 비용 절감."""
        queries = list(dict.fromkeys(queries))
        for model in MODEL_IDS:
            missing = [query for query in queries if (model, query) not in self._query_vectors]
            if not missing:
                continue
            vectors = _vectors(self.embedder.encode(missing, model=model, query=True))
            if len(vectors) != len(missing):
                raise ValueError("질문 수와 임베딩 수가 다릅니다.")
            for query, vector in zip(missing, vectors):
                self._query_vectors[model, query] = vector.reshape(1, -1)

    @classmethod
    def build(cls, corpus: DocumentCorpus, *, embedder=None):
        chunks = [chunk for doc in corpus.documents for chunk in chunk_document(doc)]
        if not chunks:
            raise ValueError("인덱싱할 본문이 없습니다. PDF 추출 결과를 확인하세요.")
        embedder = embedder or LocalEmbeddings()
        indexes = {}
        for model in MODEL_IDS:
            vectors = _vectors(embedder.encode([c.text for c in chunks], model=model))
            if len(vectors) != len(chunks):
                raise ValueError("청크 수와 임베딩 수가 다릅니다.")
            index = faiss.IndexFlatIP(vectors.shape[1])
            index.add(vectors)
            indexes[model] = index
        return cls(chunks, indexes, embedder)

    def search(self, query: str, *, company: str, top_k: int = 5,
               min_score: float = 0.30) -> list[SearchHit]:
        if not -1 <= min_score <= 1:
            raise ValueError("min_score는 -1~1 범위여야 합니다.")
        if not query.strip() or top_k <= 0:
            return []
        eligible = {i for i, chunk in enumerate(self.chunks) if chunk.company == company}
        if not eligible:
            return []
        self.prepare_queries([query])
        scores = {i: {} for i in eligible}
        for model, index in self.indexes.items():
            vector = self._query_vectors[model, query]
            if vector.shape != (1, index.d):
                raise ValueError("질문과 문서의 임베딩 차원이 다릅니다. 인덱스를 다시 생성하세요.")
            # 전체 점수 조회 후 기업 필터: 다른 기업이 상위 결과를 점유하는 문제 방지.
            distances, positions = index.search(vector, index.ntotal)
            for score, position in zip(distances[0], positions[0]):
                if int(position) in eligible:
                    scores[int(position)][model] = float(score)
        preferred = choose_embedding_model(query)
        weights = {model: 0.7 if model == preferred else 0.3 for model in MODEL_IDS}
        hits = []
        for position, model_scores in scores.items():
            score = sum(weights[m] * model_scores[m] for m in MODEL_IDS)
            if score >= min_score:
                hits.append(SearchHit(self.chunks[position], score, model_scores))
        return sorted(hits, key=lambda h: (-h.score, h.chunk.chunk_id))[:top_k]

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        # 내용 기반 파일명: 저장 중단 시 이전 메타데이터가 새 벡터를 참조하지 않음.
        chunk_data = [asdict(chunk) for chunk in self.chunks]
        fingerprint = hashlib.sha256(json.dumps(chunk_data, sort_keys=True).encode()).hexdigest()[:16]
        files = {}
        for model, index in self.indexes.items():
            raw = faiss.serialize_index(index).tobytes()
            digest = hashlib.sha256(raw).hexdigest()
            name = f"{model}-{fingerprint}-{digest[:16]}.faiss"
            (directory / name).write_bytes(raw)
            files[model] = {"name": name, "sha256": digest}
        write_json(directory / "metadata.json", {
            "schema_version": 1, "models": MODEL_IDS, "chunks": chunk_data, "files": files,
        })

    @classmethod
    def load(cls, directory: Path, *, embedder=None):
        data = read_json(directory / "metadata.json")
        if data.get("schema_version") != 1 or data.get("models") != MODEL_IDS:
            raise ValueError("인덱스 모델/버전이 다릅니다. 다시 생성하세요.")
        chunks = [Chunk(**chunk) for chunk in data["chunks"]]
        indexes = {}
        for model in MODEL_IDS:
            item = data["files"][model]
            if Path(item["name"]).name != item["name"]:
                raise ValueError("잘못된 인덱스 파일명입니다.")
            raw = (directory / item["name"]).read_bytes()
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ValueError("인덱스 무결성 검사 실패: 다시 생성하세요.")
            index = faiss.deserialize_index(np.frombuffer(raw, dtype="uint8"))
            if index.ntotal != len(chunks):
                raise ValueError("인덱스와 청크 수가 다릅니다.")
            indexes[model] = index
        return cls(chunks, indexes, embedder)

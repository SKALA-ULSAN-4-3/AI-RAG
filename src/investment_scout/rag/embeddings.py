"""검색 전용 임베딩: CPU 기본 실행, Jina 질문·문서 프롬프트 구분."""

import gc
import os

from investment_scout.retrieval.embeddings import JINA_MODEL_ID, KURE_MODEL_ID

MODEL_IDS = {"kure": KURE_MODEL_ID, "jina": JINA_MODEL_ID}


class LocalEmbeddings:
    """메모리 관리: 두 모델을 동시에 올리지 않고 현재 모델 하나만 유지."""

    def __init__(self, *, device: str | None = None, batch_size: int | None = None):
        self.device = device or os.getenv("EMBEDDING_DEVICE", "cpu")
        self.batch_size = batch_size or int(os.getenv("EMBEDDING_BATCH_SIZE", "4"))
        if self.batch_size <= 0:
            raise ValueError("EMBEDDING_BATCH_SIZE는 양수여야 합니다.")
        self._model = None
        self._choice = None

    def encode(self, texts: list[str], *, model: str, query: bool = False):
        if model not in MODEL_IDS:
            raise ValueError(f"지원하지 않는 임베딩 모델: {model}")
        if self._choice != model:
            self._model = None
            gc.collect()
            from sentence_transformers import SentenceTransformer
            import torch
            # CPU 호환: GPU 전용 Flash Attention과 BF16을 강제하지 않음.
            self._model = SentenceTransformer(
                MODEL_IDS[model], device=self.device, trust_remote_code=(model == "jina"),
                model_kwargs={"dtype": torch.float32} if model == "jina" else {},
            )
            self._choice = model
        options = {"task": "retrieval", "prompt_name": "query" if query else "document"} if model == "jina" else {}
        return self._model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True,
            convert_to_numpy=True, show_progress_bar=False, **options,
        )

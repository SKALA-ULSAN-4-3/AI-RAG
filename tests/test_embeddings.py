from investment_scout.retrieval.embeddings import (
    HybridEmbeddingRetriever,
    OpenSourceEmbeddingRouter,
    choose_embedding_model,
)


class FakeBackend:
    def __init__(self, vectors):
        self.vectors = vectors

    def encode(self, texts):
        return [self.vectors[text] for text in texts]


def test_model_selection_uses_kure_for_korean_and_jina_for_technical_mixed_text():
    assert choose_embedding_model("한국 스타트업의 핵심 제품과 투자 단계를 알려줘") == "kure"
    assert choose_embedding_model("NPU TOPS/W와 Foundry Architecture 비교") == "jina"
    assert choose_embedding_model("한국 NPU의 TOPS/W 및 CXL Architecture 비교") == "jina"


def test_hybrid_retriever_fuses_kure_and_jina_scores():
    texts = ["한국 AI 반도체", "English photonics interconnect"]
    vectors = {
        "AI 반도체 후보": [1.0, 0.0],
        texts[0]: [1.0, 0.0],
        texts[1]: [0.0, 1.0],
    }
    router = OpenSourceEmbeddingRouter(
        kure_backend=FakeBackend(vectors), jina_backend=FakeBackend(vectors)
    )
    results = HybridEmbeddingRetriever(router).search(
        "AI 반도체 후보", texts, strategy="hybrid", top_k=1
    )
    assert results[0].text == texts[0]
    assert results[0].score == 1.0


def test_auto_strategy_routes_without_loading_other_model():
    texts = ["한국어 문서", "영문 문서"]
    vectors = {
        "한국 후보를 검색": [1.0, 0.0],
        texts[0]: [1.0, 0.0],
        texts[1]: [0.0, 1.0],
    }

    def fail_factory(model_id):
        raise AssertionError(f"unexpected model load: {model_id}")

    router = OpenSourceEmbeddingRouter(
        kure_backend=FakeBackend(vectors), backend_factory=fail_factory
    )
    results = HybridEmbeddingRetriever(router).search(
        "한국 후보를 검색", texts, strategy="auto", top_k=1
    )
    assert results[0].index == 0

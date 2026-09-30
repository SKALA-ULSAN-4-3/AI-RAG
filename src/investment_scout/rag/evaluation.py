"""검색 품질 평가: 정답 구절이 있는 페이지 기준 Hit Rate@K·MRR, 모델별 비교."""

from investment_scout.rag.generation import loose_text

# 비교 대상: 실제 시스템(질문 언어별 0.7/0.3 가중치)과 단일 모델.
MODES = {
    "hybrid": None,
    "kure": {"kure": 1.0, "jina": 0.0},
    "jina": {"kure": 0.0, "jina": 1.0},
}


def relevant_pages(index, company: str, phrases: list[str]) -> set[tuple[str, int]]:
    """정답 페이지: 구절을 포함한 해당 기업 청크의 (문서, 페이지)."""
    targets = [loose_text(phrase) for phrase in phrases]
    return {
        (chunk.document_id, chunk.page) for chunk in index.chunks
        if chunk.company == company and any(t in loose_text(chunk.text) for t in targets)
    }


def evaluate(index, items: list[dict], *, ks=(1, 3, 5, 10), modes=MODES) -> dict:
    """순위 평가: 임계값 없이 상위 max(ks)개 중 정답 페이지의 첫 순위로 계산."""
    depth = max(ks)
    gold = []
    for item in items:
        pages = relevant_pages(index, item["company"], item["answer_contains"])
        if not pages:
            # 정답셋 오류: 자료에 없는 구절은 평가에서 조용히 빼지 않고 중단.
            raise ValueError(f"정답 구절을 자료에서 찾을 수 없습니다: {item}")
        gold.append(pages)
    index.prepare_queries([item["question"] for item in items])
    report = {"questions": len(items), "k": list(ks), "metrics": {}, "items": []}
    ranks_by_mode = {}
    for mode, weights in modes.items():
        ranks = []
        for item, pages in zip(items, gold):
            hits = index.search(item["question"], company=item["company"],
                                top_k=depth, min_score=-1, weights=weights)
            rank = next((i for i, hit in enumerate(hits, start=1)
                         if (hit.chunk.document_id, hit.chunk.page) in pages), None)
            ranks.append(rank)
        ranks_by_mode[mode] = ranks
        report["metrics"][mode] = {
            **{f"hit@{k}": sum(r is not None and r <= k for r in ranks) / len(ranks) for k in ks},
            f"mrr@{depth}": sum(1 / r for r in ranks if r is not None) / len(ranks),
        }
    for position, (item, pages) in enumerate(zip(items, gold)):
        report["items"].append({
            "company": item["company"], "question": item["question"],
            "relevant_pages": sorted(f"{doc}:p{page}" for doc, page in pages),
            "rank": {mode: ranks[position] for mode, ranks in ranks_by_mode.items()},
        })
    return report

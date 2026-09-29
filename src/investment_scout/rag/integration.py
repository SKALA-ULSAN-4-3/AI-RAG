"""1번 연동: 기존 탐색 결과에 페이지별 기술 출처를 함께 등록."""

from dataclasses import asdict


def with_technical_sources(scout_node, index):
    """탐색 노드 래퍼: 기술 노드의 tech_summary 단일 필드 계약 유지."""
    def node(state):
        update = scout_node(state)
        sources = {name: list(items) for name, items in update.get("source_evidence", {}).items()}
        for chunk in index.chunks:
            items = sources.setdefault(chunk.company, [])
            if any(s["source_id"] == chunk.chunk_id for s in items):
                raise ValueError(f"기술 출처 ID 충돌: {chunk.chunk_id}")
            items.append({**asdict(chunk), "source_id": chunk.chunk_id, "evidence": chunk.text,
                          "url_fetched": True, "is_mock": False, "supports": ["product"]})
        return {**update, "source_evidence": sources}
    return node

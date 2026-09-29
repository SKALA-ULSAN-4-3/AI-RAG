"""후보 기업 탐색 및 스타트업 적격성 검증 에이전트.

담당 노드: `scout_candidates`

읽는 State 필드 : target_domain, max_candidates
갱신하는 State 필드: candidate_startups, candidate_profiles, source_evidence,
                     scout_result, diagnostics

[동작 요약]
1. 검증 후보 목록을 읽는다 (data/candidates_verified.json 또는 주입된 목록).
2. 기업명 + 공식 도메인으로 중복을 제거한다.
3. live 모드면 기업별로 검색해 확인 신호(signal)와 출처를 수집한다.
   - 검색으로 "확인된" 것만 프로필에 반영한다.
   - 인수 기사를 찾지 못한 경우 exit_status 는 NOT_FOUND 로 두고
     "Exit 없음"으로 확정하지 않는다.
4. 적격성을 판정한다 (ELIGIBLE / INELIGIBLE / NEEDS_VERIFICATION).
5. ELIGIBLE 기업만 candidate_startups 에 넣고, 나머지는 사유와 함께
   scout_result 에 보존한다.
6. 한국/해외별 확보 수와 목표 대비 부족 수를 scout_result 에 표시한다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from investment_scout.contracts import validate_node_update
from investment_scout.eligibility import (
    ELIGIBLE,
    EXIT_ACQUIRED,
    EXIT_IPO,
    EXIT_NOT_CHECKED,
    EXIT_NOT_FOUND,
    EXIT_SIGNAL_FOUND,
    INELIGIBLE,
    NEEDS_VERIFICATION,
    judge_eligibility,
)
from investment_scout.evidence import make_source_id, new_source, utc_now_iso, validate_source
from investment_scout.search.base import (
    MODE_LIVE,
    SearchConfigurationError,
    SearchFailure,
    SearchHit,
    SearchProvider,
)
from investment_scout.state import (
    DIAG_DATA_GAP,
    DIAG_SYSTEM_ERROR,
    InvestmentAgentState,
    TERMINATION_NO_ELIGIBLE_CANDIDATES,
    TERMINATION_ZERO_LIMIT,
    make_diagnostic,
    validate_max_candidates,
)

DEFAULT_SEED_PATH = Path(__file__).resolve().parents[3] / "data" / "candidates_verified.json"

SCOUT_NODE_FIELDS = (
    "candidate_startups",
    "candidate_profiles",
    "source_evidence",
    "scout_result",
    "diagnostics",
    # 평가 없이 바로 보고서로 가는 두 경우(적격 후보 없음 / 한도 0)의 종료 사유를
    # 남깁니다. 분기 함수는 State 를 갱신할 수 없어 노드에서 설정합니다.
    "termination_reason",
)

# 국가별 목표 후보 수 (목표를 채우려고 기업이나 출처를 만들어내지 않습니다 — 부족분은 부족분으로 보고)
DEFAULT_TARGETS = {"KR": 10, "OVERSEAS": 10}

# 검색 스니펫에서 찾는 신호 패턴.
# 신호는 "확인해야 할 단서"이지 그 자체로 확정된 사실이 아닙니다.
_ACQUISITION_PATTERNS = (
    r"\bacquired by\b",
    r"\bacquisition of\b",
    r"인수(?:됐|되었|했|한다|합니다)",
    r"피인수",
)
_IPO_PATTERNS = (
    r"\bIPO\b",
    r"\bgoes public\b",
    r"\blisted on (?:the )?(?:NASDAQ|NYSE|KOSDAQ|KOSPI)\b",
    r"코스닥\s*상장",
    r"코스피\s*상장",
)
_STAGE_PATTERNS = {
    "SERIES_C": (r"series\s*c\b", r"시리즈\s*c"),
    "SERIES_B": (r"series\s*b\b", r"시리즈\s*b"),
    "SERIES_A": (r"series\s*a\b", r"시리즈\s*a"),
    "PRE_SERIES_A": (r"pre[-\s]?series\s*a\b", r"프리\s*시리즈\s*a"),
    "SEED": (r"\bseed round\b", r"시드\s*(?:라운드|투자)"),
    "SERIES_D": (r"series\s*d\b", r"시리즈\s*d"),
    "SERIES_E": (r"series\s*e\b", r"시리즈\s*e"),
}


def normalize_name(name: str) -> str:
    """중복 제거용 기업명 정규화."""
    lowered = name.strip().lower()
    lowered = re.sub(r"\b(inc|corp|corporation|ltd|limited|llc|co|company|주식회사|㈜)\b\.?", " ", lowered)
    return re.sub(r"[^0-9a-z가-힣]+", "", lowered)


def registrable_domain(url: Optional[str]) -> Optional[str]:
    """공식 홈페이지 URL 에서 중복 제거용 도메인을 뽑습니다."""
    if not url:
        return None
    parsed = urlparse(url if "//" in url else f"https://{url}")
    host = (parsed.netloc or parsed.path).strip().lower()
    host = host.split("@")[-1].split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    return host or None


def dedupe_profiles(
    profiles: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """기업명과 공식 도메인 기준으로 중복을 제거합니다.

    반환: (고유 프로필 목록, 중복 기록 목록)
    """
    unique: List[Dict[str, Any]] = []
    duplicates: List[Dict[str, Any]] = []
    seen_names: Dict[str, str] = {}
    seen_domains: Dict[str, str] = {}

    for profile in profiles:
        name = profile.get("name", "")
        keys = {normalize_name(name), *(normalize_name(a) for a in profile.get("aliases") or [])}
        keys.discard("")
        domain = registrable_domain(profile.get("website"))

        hit_name = next((seen_names[k] for k in keys if k in seen_names), None)
        hit_domain = seen_domains.get(domain) if domain else None
        kept_as = hit_name or hit_domain

        if kept_as:
            duplicates.append(
                {
                    "name": name,
                    "kept_as": kept_as,
                    "matched_by": "name" if hit_name else "domain",
                    "domain": domain,
                }
            )
            continue

        for key in keys:
            seen_names[key] = name
        if domain:
            seen_domains[domain] = name
        unique.append(profile)

    return unique, duplicates


def _matches(text: str, patterns: Sequence[str]) -> bool:
    return any(re.search(p, text, flags=re.IGNORECASE) for p in patterns)


# 검색 질의에 "acquisition OR IPO" 가 들어가므로, 그 단어가 있을 뿐 **다른 기업을 다룬 기사**가
# 결과에 섞여 들어옵니다. 기업명(별칭 포함)이 본문에 나오지 않으면 그 기업의 신호로 보지 않습니다.
def mentions_company(text: str, company_names: Sequence[str]) -> bool:
    """검색 결과 텍스트가 해당 기업(또는 별칭)을 실제로 언급하는지 확인합니다."""
    normalized_text = normalize_name(text)
    for name in company_names:
        key = normalize_name(name)
        # 2자 이하 약칭은 우연히 포함될 확률이 높아 근거로 쓰지 않습니다.
        if len(key) >= 3 and key in normalized_text:
            return True
    return False


def extract_signals(
    hits: Sequence[SearchHit],
    *,
    company_names: Optional[Sequence[str]] = None,
) -> List[Dict[str, Any]]:
    """검색 결과에서 확인 신호를 추출합니다.

    신호는 "이 자료가 이런 내용을 언급한다"는 단서일 뿐이며,
    프로필 필드를 곧바로 확정하지 않습니다.

    company_names 를 주면 그 기업을 언급하지 않는 자료는 건너뜁니다.
    """
    signals: List[Dict[str, Any]] = []
    for index, hit in enumerate(hits):
        text = f"{hit.get('title', '')} {hit.get('snippet', '')}"
        if company_names and not mentions_company(text, company_names):
            continue
        if _matches(text, _ACQUISITION_PATTERNS):
            signals.append({"topic": "exit_status", "value": EXIT_ACQUIRED, "hit_index": index})
        if _matches(text, _IPO_PATTERNS):
            signals.append({"topic": "public_listing", "value": EXIT_IPO, "hit_index": index})
        for stage, patterns in _STAGE_PATTERNS.items():
            if _matches(text, patterns):
                signals.append({"topic": "funding_stage", "value": stage, "hit_index": index})
                break
    return signals


class StartupScout:
    """후보 탐색 및 적격성 검증 에이전트."""

    def __init__(
        self,
        *,
        search_provider: SearchProvider,
        seed_profiles: Optional[Sequence[Dict[str, Any]]] = None,
        seed_path: Optional[Path] = None,
        targets: Optional[Dict[str, int]] = None,
        max_queries_per_company: int = 3,
    ) -> None:
        self.search = search_provider
        self.seed_path = seed_path or DEFAULT_SEED_PATH
        self._seed_profiles = list(seed_profiles) if seed_profiles is not None else None
        self.targets = dict(targets or DEFAULT_TARGETS)
        self.max_queries_per_company = max_queries_per_company

    # ------------------------------------------------------------------
    def load_seed_profiles(self, target_domain: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """시드 후보 목록을 읽습니다."""
        if self._seed_profiles is not None:
            return [dict(p) for p in self._seed_profiles], {
                "source": "injected",
                "target_domain": target_domain,
            }

        if not self.seed_path.exists():
            return [], {"source": "missing", "path": str(self.seed_path)}

        doc = json.loads(self.seed_path.read_text(encoding="utf-8"))
        meta = {
            "source": str(self.seed_path),
            "data_origin": doc.get("data_origin"),
            "seed_basis": doc.get("seed_basis"),
            "compiled_at": doc.get("compiled_at"),
            "target_domain": doc.get("target_domain"),
            "disclaimer": doc.get("disclaimer"),
        }
        if doc.get("targets"):
            self.targets = dict(doc["targets"])
        return [dict(p) for p in doc.get("companies", [])], meta

    # ------------------------------------------------------------------
    def verify_with_search(
        self,
        profile: Dict[str, Any],
        target_domain: str,
        *,
        preserve_verified_exit: bool = False,
    ) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """검색으로 기업 정보를 확인합니다.

        반환: (갱신된 프로필, 출처 목록, 진단 목록)

        중요: 검색으로 확정할 수 있는 것만 반영합니다.
        - 인수/상장 신호를 찾으면 exit_status 를 그 값으로 설정합니다.
        - 찾지 못하면 exit_status 는 NOT_FOUND 로 둡니다 ("Exit 없음"이 아님).
        - 투자 단계는 신호만 기록하고 확정하지 않습니다. 최신 자료의 게시일이
          확인될 때만 funding_stage_confirmed_at 을 채웁니다.
        """
        name = profile["name"]
        queries = [
            f"{name} {target_domain} AI startup funding round",
            f"{name} acquisition OR acquired OR IPO",
            f"{name} 투자 유치 시리즈",
        ][: self.max_queries_per_company]

        sources: List[Dict[str, Any]] = []
        diagnostics: List[Dict[str, Any]] = []
        all_hits: List[SearchHit] = []

        for query in queries:
            try:
                hits = self.search.search(query, max_results=5)
            except SearchConfigurationError as exc:
                # 설정 오류는 자료 부족이 아니라 시스템 오류이며, 숨기지 않고 올립니다.
                raise
            except SearchFailure as exc:
                diagnostics.append(
                    make_diagnostic(
                        kind="SEARCH_FAILURE",
                        category=DIAG_SYSTEM_ERROR,
                        message=f"검색 호출 실패: {exc}",
                        startup=name,
                        details={"query": query},
                    )
                )
                continue
            all_hits.extend(hits)

        if not all_hits:
            diagnostics.append(
                make_diagnostic(
                    kind="NO_SEARCH_RESULTS",
                    category=DIAG_DATA_GAP,
                    message="검색 결과가 없어 확인할 자료를 찾지 못했습니다",
                    startup=name,
                    details={"queries": queries},
                )
            )

        signals = extract_signals(
            all_hits, company_names=[name, *(profile.get("aliases") or [])]
        )
        signal_hit_indexes = {s["hit_index"] for s in signals}

        # 신호가 나온 자료만 출처로 등록합니다 (실제로 확인한 자료만 등록).
        accessed_at = utc_now_iso()
        index_to_source_id: Dict[int, str] = {}
        for order, hit_index in enumerate(sorted(signal_hit_indexes), start=1):
            hit = all_hits[hit_index]
            supports = sorted(
                {
                    {"exit_status": "exit_status", "public_listing": "public_listing", "funding_stage": "funding_stage"}[
                        s["topic"]
                    ]
                    for s in signals
                    if s["hit_index"] == hit_index
                }
            )
            # 검증된 시드의 source_id(001, 002, ...)와 충돌하지 않도록 live 검색은
            # 별도 번호 대역을 사용합니다.
            source_id = make_source_id(name, 100 + order)
            index_to_source_id[hit_index] = source_id
            sources.append(
                new_source(
                    source_id=source_id,
                    url=hit.get("url", ""),
                    title=hit.get("title", ""),
                    publisher=hit.get("publisher") or registrable_domain(hit.get("url")) or "UNKNOWN",
                    published_at=hit.get("published_at"),
                    accessed_at=accessed_at,
                    evidence=hit.get("snippet", ""),
                    supports=supports,
                    url_fetched=True,
                    is_mock=bool(hit.get("is_mock")),
                )
            )

        updated = dict(profile)
        updated["search_signals"] = [
            {**s, "source_id": index_to_source_id.get(s["hit_index"])} for s in signals
        ]
        updated["source_ids"] = [s["source_id"] for s in sources]
        updated["verification_performed"] = True
        updated["verification_queries"] = queries

        # Exit / 상장 판정
        # 키워드 일치는 "확인해야 할 단서"이지 확정된 사실이 아닙니다. 검색만으로 부적격을
        # 확정하지 않고 검증대기(EXIT_SIGNAL_FOUND)로 보내 사람이 1차 자료로 확인하게 합니다.
        # is_public 도 검색 신호로는 바꾸지 않습니다 (상장 여부는 공시로만 확정).
        exit_signal = next((s for s in signals if s["topic"] == "exit_status"), None)
        ipo_signal = next((s for s in signals if s["topic"] == "public_listing"), None)
        if exit_signal or ipo_signal:
            updated["exit_status"] = EXIT_SIGNAL_FOUND
            updated["exit_signal_kind"] = "ACQUISITION" if exit_signal else "IPO"
            updated["exit_signal_source_id"] = index_to_source_id.get(
                (exit_signal or ipo_signal)["hit_index"]
            )
        elif not (
            preserve_verified_exit and updated.get("exit_status") == "NONE_CONFIRMED"
        ):
            # 인수 기사를 찾지 못한 것일 뿐, Exit 이 없다고 새로 확정하지 않습니다.
            # 다만 이미 최신 근거로 NONE_CONFIRMED 된 검증 시드는 유지합니다.
            updated["exit_status"] = EXIT_NOT_FOUND

        # 투자 단계: 게시일이 확인된 자료에서 나온 신호만 확정 후보로 봅니다.
        stage_signals = [s for s in signals if s["topic"] == "funding_stage"]
        dated = [
            (s, all_hits[s["hit_index"]].get("published_at"))
            for s in stage_signals
            if all_hits[s["hit_index"]].get("published_at")
        ]
        if dated:
            newest = max(dated, key=lambda item: item[1])
            updated["funding_stage"] = newest[0]["value"]
            updated["funding_stage_confirmed_at"] = newest[1]
        elif stage_signals:
            diagnostics.append(
                make_diagnostic(
                    kind="STAGE_DATE_UNKNOWN",
                    category=DIAG_DATA_GAP,
                    message="투자 단계 신호는 찾았으나 자료 게시일을 확인할 수 없어 현재 단계로 확정하지 않았습니다",
                    startup=name,
                    details={"signals": [s["value"] for s in stage_signals]},
                )
            )

        return updated, sources, diagnostics

    # ------------------------------------------------------------------
    def run(self, target_domain: str) -> Dict[str, Any]:
        """탐색 전체를 실행하고 State 업데이트 딕셔너리를 돌려줍니다."""
        seed_profiles, seed_meta = self.load_seed_profiles(target_domain)
        unique_profiles, duplicates = dedupe_profiles(seed_profiles)

        diagnostics: List[Dict[str, Any]] = []
        source_evidence: Dict[str, List[Dict[str, Any]]] = {}
        candidate_profiles: Dict[str, Dict[str, Any]] = {}

        eligible: List[str] = []
        ineligible: List[Dict[str, Any]] = []
        needs_verification: List[Dict[str, Any]] = []

        live_mode = getattr(self.search, "mode", None) == MODE_LIVE
        seed_verified = seed_meta.get("data_origin") == "VERIFIED_WEB_RESEARCH"

        for profile in unique_profiles:
            name = profile["name"]

            if live_mode:
                profile, sources, diags = self.verify_with_search(
                    profile,
                    target_domain,
                    preserve_verified_exit=seed_verified,
                )
                diagnostics.extend(diags)
                if sources:
                    source_evidence[name] = sources
            else:
                profile = dict(profile)
                profile.setdefault("verification_performed", seed_verified)
                profile.setdefault("exit_status", EXIT_NOT_CHECKED)

            # 시드 프로필에 이미 확인된 출처가 기록돼 있으면 그대로 등록합니다.
            # (실제 시드 데이터에는 출처가 없고, mock 픽스처에만 is_mock=True 출처가 있습니다.)
            seed_sources = profile.pop("sources", None)
            if seed_sources:
                for src in seed_sources:
                    validate_source(src, where=f"{name}.sources")
                source_evidence.setdefault(name, []).extend(seed_sources)
                # live 검색을 함께 실행한 경우 새 검색 출처 ID도 잃지 않습니다.
                profile["source_ids"] = list(
                    dict.fromkeys(
                        [
                            *(profile.get("source_ids") or []),
                            *(s["source_id"] for s in seed_sources),
                        ]
                    )
                )
            profile.setdefault("source_ids", [])

            verdict = judge_eligibility(profile)
            profile["eligibility"] = verdict
            candidate_profiles[name] = profile

            if verdict["verdict"] == ELIGIBLE:
                eligible.append(name)
            elif verdict["verdict"] == INELIGIBLE:
                ineligible.append({"name": name, "reasons": verdict["reasons"]})
            else:
                needs_verification.append(
                    {
                        "name": name,
                        "reasons": verdict["reasons"],
                        "unverified_fields": verdict["unverified_fields"],
                    }
                )
                diagnostics.append(
                    make_diagnostic(
                        kind="CANDIDATE_NEEDS_VERIFICATION",
                        category=DIAG_DATA_GAP,
                        message="적격성 확인에 필요한 정보가 부족해 평가 목록에서 제외했습니다",
                        startup=name,
                        details={"unverified_fields": verdict["unverified_fields"]},
                    )
                )

        counts = self._region_counts(candidate_profiles, eligible)

        effective_search_mode = (
            "verified_dataset"
            if seed_verified and not live_mode
            else getattr(self.search, "mode", "unknown")
        )
        scout_result = {
            "target_domain": target_domain,
            "search_mode": effective_search_mode,
            "verification_performed": live_mode or seed_verified,
            "seed_meta": seed_meta,
            "targets": dict(self.targets),
            "counts": counts,
            "eligible": list(eligible),
            "ineligible": ineligible,
            "needs_verification": needs_verification,
            "duplicates_removed": duplicates,
            "totals": {
                "seed": len(seed_profiles),
                "after_dedupe": len(unique_profiles),
                "eligible": len(eligible),
                "ineligible": len(ineligible),
                "needs_verification": len(needs_verification),
            },
            "shortfall_note": (
                "목표 수를 채우기 위해 기업이나 출처를 생성하지 않았습니다. "
                "확보된 ELIGIBLE 후보만으로 평가를 진행합니다."
            ),
        }

        return {
            "candidate_startups": eligible,
            "candidate_profiles": candidate_profiles,
            "source_evidence": source_evidence,
            "scout_result": scout_result,
            "diagnostics": diagnostics,
        }

    def _region_counts(
        self, profiles: Dict[str, Dict[str, Any]], eligible: Sequence[str]
    ) -> Dict[str, Dict[str, Any]]:
        counts: Dict[str, Dict[str, Any]] = {}
        for region, target in self.targets.items():
            secured = sum(1 for name in eligible if profiles.get(name, {}).get("region") == region)
            counts[region] = {
                "target": target,
                "secured": secured,
                "shortfall": max(0, target - secured),
                "screened": sum(1 for p in profiles.values() if p.get("region") == region),
            }
        return counts


def make_scout_node(scout: StartupScout):
    """StartupScout 를 LangGraph 노드 함수로 감쌉니다(그래프에는 이 반환값을 add_node 로 등록)."""

    # 후보 탐색 노드
    #   읽는 필드   : target_domain, max_candidates, diagnostics
    #   갱신하는 필드: candidate_startups, candidate_profiles, source_evidence, scout_result,
    #                 diagnostics, termination_reason
    def scout_candidates(state: InvestmentAgentState) -> Dict[str, Any]:
        """시드 후보를 적격성 판정해 ELIGIBLE 만 candidate_startups 에 올립니다."""
        update = scout.run(state["target_domain"])

        # 기존 diagnostics 뒤에 이어붙인 "전체 리스트"를 반환합니다 (overwrite 규칙).
        update["diagnostics"] = [*state.get("diagnostics", []), *update["diagnostics"]]

        # 평가 없이 종료되는 두 경우(한도 0 / 적격 후보 없음)의 사유를 남깁니다.
        max_candidates = validate_max_candidates(state.get("max_candidates", 0))
        if max_candidates == 0:
            update["termination_reason"] = TERMINATION_ZERO_LIMIT
        elif not update["candidate_startups"]:
            update["termination_reason"] = TERMINATION_NO_ELIGIBLE_CANDIDATES
        else:
            update["termination_reason"] = ""

        return validate_node_update(
            update, allowed_fields=SCOUT_NODE_FIELDS, node_name="scout_candidates"
        )

    return scout_candidates

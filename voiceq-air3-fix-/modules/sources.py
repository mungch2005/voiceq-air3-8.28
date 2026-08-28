"""화면 소스 카탈로그 — 폐쇄 목록 관리와 후보 선별(retrieval).

두 가지 역할을 한다.

1) 폐쇄 목록 강제
   LLM은 이 카탈로그에 있는 source_id만 낼 수 있다. 자유롭게 이름을 지어내면
   실제로 존재하지 않는 화면을 띄우라는 명령이 되어 실행 단계에서 깨진다.
   프롬프트 지시만으로는 새기 때문에 apply 단계에서 코드로도 걸러낸다.

2) 후보 선별
   카탈로그가 245건이라 전부 프롬프트에 넣으면 입력 토큰이 폭증해 응답이 느려진다.
   발언 내용과 현재 항적 위치를 근거로 20건 내외를 골라 LLM에 넘긴다.
   기획서의 "수천 개 자료 중 상황에 맞는 것을 선별" 단계가 바로 이것이다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from modules import base_map as bm

CATALOG_PATH = Path(__file__).resolve().parent.parent / "data" / "screen_sources.json"

# 상황과 무관하게 항상 후보에 포함할 기준 화면.
ALWAYS_CANDIDATES = ["SYS-BASEMAP", "SYS-SITBOARD", "SYS-ADS"]


@lru_cache(maxsize=1)
def load_catalog() -> list[dict]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["sources"]


@lru_cache(maxsize=1)
def by_id() -> dict[str, dict]:
    return {s["id"]: s for s in load_catalog()}


def exists(source_id: str) -> bool:
    return source_id in by_id()


def name_of(source_id: str) -> str:
    source = by_id().get(source_id)
    return source["name"] if source else source_id


# ---------- 지역 그룹핑 ----------

_PERIMETER_RE = re.compile(r"^CCTV-PERI-([NSWE])\d+$")


@lru_cache(maxsize=1)
def _region_codes() -> set[str]:
    """지역으로 인정할 코드 집합(시설 26곳 + 초소 8곳 + 대공자산 7곳)."""
    base = bm.load_base_map()
    return {f["id"] for f in base["facilities"]} \
        | {g["id"] for g in base["sentry_posts"]} \
        | {a["id"] for a in base["air_defense"]}


def region_of(source: dict) -> str:
    """카메라가 속한 '지역'. 같은 지역 카메라는 한 슬롯에 하나만 고른다(playbook._best_matches).

    시설 하나를 외부/출입구/내부처럼 여러 각도로 찍거나(탄약고만 7대), 초소
    하나를 CCTV+바디캠 2대로, 대공자산 하나를 CCTV+조준경 2대로 찍는 경우가
    있다 — 이런 카메라의 ID는 항상 "{피드유형}-{코드}-{각도}" 형태라, 접두어
    바로 다음 자리에 시설·초소·대공자산 코드가 들어간다. 그 자리에 있는
    코드만 지역으로 인정한다.

    아무 자리나 부분 문자열로 찾으면 안 된다 — "출입구"를 뜻하는 접미어가
    모든 시설에서 공통으로 "-GATE"라서, "발전소 출입구 CCTV"(CCTV-POW-GATE)가
    "기지 정문" 시설 코드(GATE)와 우연히 겹쳐 정문 지역으로 잘못 묶인 적이
    있었다. 접두어 바로 뒤 1~2토큰만 후보로 본다("GATE-R"처럼 코드 자체가
    하이픈을 포함하는 경우가 있어 2토큰까지 시도).

    외곽 울타리는 방위별로 구간이 7~8개씩 있고 이 자리 규칙으로는 안 걸리므로
    별도 정규식으로 방위 단위로 묶는다. 둘 다 안 걸리면 그 카메라 자체가
    유일한 지역이다(기존 동작과 동일).
    """
    sid = source["id"]
    m = _PERIMETER_RE.match(sid)
    if m:
        return f"PERI-{m.group(1)}"
    tokens = sid.split("-")
    if len(tokens) < 2:
        return sid
    rest = tokens[1:]
    codes = _region_codes()
    for n in (2, 1):
        if len(rest) >= n:
            candidate = "-".join(rest[:n])
            if candidate in codes:
                return candidate
    return sid


def cell_of(source_id: str) -> str:
    source = by_id().get(source_id)
    return source["cell"] if source else ""


# ---------- 후보 선별 ----------


# 복합 방위를 먼저 잡아야 "북서"가 "북"+"서"로 쪼개지지 않는다.
DIRECTIONS = ["북서", "북동", "남서", "남동", "북", "남", "동", "서"]


def mentioned_directions(text: str) -> list[str]:
    found, remaining = [], text
    for direction in DIRECTIONS:
        if direction in remaining:
            found.append(direction)
            remaining = remaining.replace(direction, "")
    return found


@lru_cache(maxsize=1)
def _facility_names() -> list[str]:
    """base_map.json에 등록된 실제 시설명. 발언에서 "장소"를 인식하는 기준 어휘.

    긴 이름부터 검사해야 "활주로"가 "활주로 09/27"보다 먼저 잡혀 엉뚱하게
    쪼개지는 일을 막는다.
    """
    names = [f["name"] for f in bm.load_base_map()["facilities"]]
    return sorted(set(names), key=len, reverse=True)


def mentioned_locations(text: str) -> list[str]:
    """발언에 그대로 등장하는 시설명들. 발언에 나온 순서대로 돌려준다.

    "탄약고 부근, 발전소 부근"처럼 서로 다른 장소가 여러 번 언급됐을 때,
    카메라가 많은 장소 하나가 후보를 독식하지 않도록 각 장소를 먼저 알아내는
    용도다(_best_matches에서 장소별로 최소 1대씩 우선 배정할 때 쓴다).
    """
    if not text:
        return []
    found, remaining = [], text
    for name in _facility_names():
        if name in remaining:
            found.append(name)
            remaining = remaining.replace(name, "")
    found.sort(key=lambda n: text.find(n))
    return found


def text_score(source: dict, text: str, mentioned_dirs: list[str]) -> float:
    """발언·맥락 텍스트와 소스의 태그·명칭이 얼마나 겹치는지.

    한국어 형태소 분석기를 쓰지 않고 부분 문자열 매칭만 한다. 태그가 "무인기",
    "활주로" 같은 명사라 조사가 붙어도("무인기가") 그대로 걸린다. 좌표 없이
    카메라를 고르는 방법이 이것뿐이므로, 플레이북 슬롯 해석(playbook.py)도
    이 함수를 그대로 쓴다.
    """
    if not text:
        return 0.0
    score = sum(3.0 for tag in source["tags"] if tag in text)
    if source["name"] in text:
        score += 4.0
    for token in source["name"].split():
        if len(token) >= 2 and token in text:
            score += 1.0

    # 발언에 방위가 나왔는데 소스가 다른 방위를 담당하면 감점.
    # "북서방 무인기"에 동측 울타리 카메라가 올라오는 것을 막는다.
    if mentioned_dirs:
        source_dirs = [d for d in source["tags"] if d in DIRECTIONS]
        if source_dirs:
            # 문자열을 set()에 넣으면 글자 단위로 쪼개진다("북서" -> {"북","서"}).
            # 그 결과 "북"만 언급했는데 "북서" 태그와도 겹친다고 오판했다 — 정확히
            # 같은 방위 단어일 때만 겹친 것으로 본다.
            overlap = bool(set(mentioned_dirs) & set(source_dirs))
            score += 3.0 if overlap else -4.0
    return score


def _proximity_score(source: dict, track_cells: list[str]) -> float:
    """항적 근처의 감시자산일수록 높은 점수. 공간 근접성은 코드가 계산한다."""
    best = 0.0
    for cell in track_cells:
        dist = bm.cell_distance(source["cell"], cell)
        if dist is None:
            continue
        if dist <= 1.5:
            best = max(best, 6.0)
        elif dist <= 3.0:
            best = max(best, 3.5)
        elif dist <= 5.0:
            best = max(best, 1.5)
    return best


def shortlist(utterance: str, context_summary: str = "", limit: int = 20) -> list[dict]:
    """이번 상황에 쓸 만한 화면 소스 후보를 추린다."""
    track_cells = [t["cell"] for t in _current_tracks()]
    haystack = f"{utterance} {context_summary}"
    # 방위는 발언 본문에서만 뽑는다. 누적 요약의 옛 방위가 현재 판단을 흐리지 않도록.
    mentioned_dirs = mentioned_directions(utterance)

    scored = []
    for source in load_catalog():
        score = text_score(source, haystack, mentioned_dirs)
        score += _proximity_score(source, track_cells)
        # priority_hint가 낮을수록(중요할수록) 약간 가산.
        score += (6 - source["priority_hint"]) * 0.4
        if score > 0:
            scored.append((score, source))

    scored.sort(key=lambda x: (-x[0], x[1]["id"]))
    picked = [s for _, s in scored[:limit]]

    picked_ids = {s["id"] for s in picked}
    for always in ALWAYS_CANDIDATES:
        source = by_id().get(always)
        if source and always not in picked_ids:
            picked.append(source)
    return picked


def _current_tracks() -> list[dict]:
    """세션에 항적이 있으면 사용. Streamlit 밖(테스트)에서도 안전하게 동작."""
    try:
        import streamlit as st

        return st.session_state.get("map_tracks", []) or []
    except Exception:
        return []


def format_for_llm(candidates: list[dict]) -> str:
    """후보 목록을 프롬프트에 넣을 압축 텍스트로. id · 명칭 · 위치 · 설명 순."""
    lines = []
    for source in candidates:
        lines.append(
            f"- {source['id']} | {source['name']} | {source['cell']} | {source['description']}"
        )
    return "\n".join(lines)


# ---------- 출력 검증 ----------


def sanitize_layout(cop_layout: list) -> tuple[list[dict], list[str]]:
    """LLM이 낸 레이아웃에서 카탈로그에 없는 소스를 제거한다.

    반환: (정제된 레이아웃, 버려진 source_id 목록)
    버려진 목록은 UI에 노출해 모델이 무엇을 지어냈는지 팀이 볼 수 있게 한다.
    """
    valid_positions = {"좌측대형", "우측상단", "우측하단", "중앙"}
    cleaned: list[dict] = []
    dropped: list[str] = []

    if not isinstance(cop_layout, list):
        return cleaned, dropped

    seen = set()
    for item in cop_layout:
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("source_id", "") or item.get("source", "") or "").strip()
        if not exists(source_id):
            if source_id:
                dropped.append(source_id)
            continue
        if source_id in seen:
            continue
        seen.add(source_id)

        position = str(item.get("position", "") or "").strip()
        if position not in valid_positions:
            position = "중앙"
        try:
            priority = int(item.get("priority", 99))
        except (TypeError, ValueError):
            priority = 99

        cleaned.append(
            {
                "source_id": source_id,
                "name": name_of(source_id),
                "cell": cell_of(source_id),
                "position": position,
                "priority": priority,
            }
        )

    cleaned.sort(key=lambda x: x["priority"])
    return cleaned, dropped

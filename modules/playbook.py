"""상황 유형별 COP 화면 구성 플레이북 — 운용자가 만든 정답 레이아웃.

설계 의도:
    화면 배치를 LLM이 매번 자유롭게 정하면 같은 상황에도 결과가 흔들린다.
    운용 경험이 있는 사람이 "이 상황에는 이 화면을 이 순서로"를 이미 알고 있으므로,
    그 판단을 데이터로 고정하고 LLM에게는 상황 유형 분류만 맡긴다.

    이 구조가 기획서의 "전문가 정답 레이아웃 대비 일치율 90% 이상" 지표와 직결된다.
    정답이 곧 이 플레이북이므로, 플레이북을 따르면 일치율은 정의상 100%가 되고
    남는 평가 대상은 "상황 유형을 맞게 분류했는가"로 좁혀진다.

    "해당 지역 cctv" 같은 슬롯은 고정할 수 없다. 발언마다 관련된 방향·시설이
    다르기 때문이다. LLM에게 좌표를 판단시키는 대신, 발언 텍스트를 코드가 직접
    읽어 방위 단어·시설명과 태그가 가장 많이 겹치는 카메라를 고른다(sources.text_score).
"""

from __future__ import annotations

import json
from pathlib import Path

from modules import sources

PLAYBOOK_PATH = Path(__file__).resolve().parent.parent / "data" / "cop_playbook.json"

GRID_ROWS, GRID_COLS = 2, 6
MAX_PANELS = GRID_ROWS * GRID_COLS


def _distribute(total: int, weights: list[float]) -> list[int]:
    """total 칸을 가중치 비율로 나눈다. 각자 최소 1칸은 갖고, 합은 정확히 total."""
    n = len(weights)
    if n == 0:
        return []
    total = max(total, n)
    widths = [1] * n
    rest = total - n
    if rest > 0:
        share = sum(weights) or 1.0
        raw = [w / share * rest for w in weights]
        floors = [int(x) for x in raw]
        order = sorted(range(n), key=lambda i: -(raw[i] - floors[i]))
        for i in order[: rest - sum(floors)]:
            floors[i] += 1
        widths = [b + f for b, f in zip(widths, floors)]
    return widths


def tiling_for(n: int) -> list[tuple[int, int, int, int]]:
    """패널 n개를 2행 6열에 빈틈없이 배치한다.

    개수에 따라 매번 다시 계산하므로 화면이 2개든 9개든 벽에 빈칸이 남지 않는다.
    우선순위가 높을수록 넓은 자리를 갖고, 1순위는 위아래를 관통하는 대형 화면이 된다.
    반환값은 (행, 열, 행병합, 열병합) 목록이며 우선순위 순서와 일치한다.
    """
    if n <= 0:
        return []
    n = min(n, MAX_PANELS)
    if n == 1:
        return [(1, 1, GRID_ROWS, GRID_COLS)]

    # 순위가 낮아질수록 완만하게 줄어드는 가중치. 급격히 줄이면 하위 화면이 너무 작아진다.
    weights = [1.0 / (i + 1) ** 0.7 for i in range(n)]

    rest = n - 1
    # 나머지 패널을 2행으로 나눠 담을 때 최소로 필요한 열 수.
    need_cols = (rest + 1) // 2
    max_first = max(1, GRID_COLS - need_cols)
    first_w = round(GRID_COLS * weights[0] / sum(weights))
    first_w = max(1, min(max_first, first_w))
    if max_first >= 2:
        first_w = max(first_w, 2)  # 1순위는 최소 2열은 확보한다.

    slots = [(1, 1, GRID_ROWS, first_w)]
    free_cols = GRID_COLS - first_w
    start_col = 1 + first_w

    if rest <= free_cols:
        # 남은 패널이 열 수보다 적으면 각자 위아래를 관통하는 세로 패널이 된다.
        col = start_col
        for w in _distribute(free_cols, weights[1:]):
            slots.append((1, col, GRID_ROWS, w))
            col += w
    else:
        top_n = (rest + 1) // 2
        for row, part in ((1, weights[1 : 1 + top_n]), (2, weights[1 + top_n :])):
            col = start_col
            for w in _distribute(free_cols, part):
                slots.append((row, col, 1, w))
                col += w
    return slots


def position_label(slot: tuple[int, int, int, int]) -> str:
    row, col, rspan, cspan = slot
    size = f" ({rspan}행×{cspan}열)" if (rspan > 1 or cspan > 1) else ""
    return f"{row}행{col}열{size}"

_cache: dict | None = None


def load_playbook(force: bool = False) -> dict:
    global _cache
    if _cache is None or force:
        _cache = json.loads(PLAYBOOK_PATH.read_text(encoding="utf-8"))
    return _cache


def save_playbook(data: dict) -> None:
    """UI에서 편집한 내용을 파일로 저장하고 캐시를 갱신한다."""
    global _cache
    PLAYBOOK_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _cache = data


def situation_names() -> list[str]:
    return [s["name"] for s in load_playbook()["situations"]]


def find_situation(name: str) -> dict | None:
    if not name:
        return None
    target = name.strip()
    for situation in load_playbook()["situations"]:
        if situation["name"] == target:
            return situation
    # 모델이 이름을 약간 다르게 쓰는 경우까지 흡수한다.
    for situation in load_playbook()["situations"]:
        if target in situation["name"] or situation["name"] in target:
            return situation
    return None


def describe_for_llm() -> str:
    """LLM에 넘길 상황 유형 목록. 화면 구성은 코드가 하므로 이름과 단서만 준다."""
    lines = []
    for situation in load_playbook()["situations"]:
        hints = ", ".join(situation.get("keywords", [])[:5])
        lines.append(f"- {situation['name']}" + (f" (단서: {hints})" if hints else ""))
    return "\n".join(lines)


# ---------- 슬롯 해석 ----------


def _best_match(
    candidates: list[dict], utterance: str, used: set[str] | None = None
) -> dict | None:
    """발언 텍스트와 가장 관련 있는 소스를 고른다. 관련 단서가 없으면 남은 후보 중 첫 항목.

    이미 다른 슬롯이 쓴 소스는 후보에서 뺀다. "해당 지역 cctv"와 "낙탄 지역 cctv"처럼
    서로 다른 슬롯이 같은 후보 풀(전체 CCTV)을 같은 발언으로 검색하면 항상 똑같은
    1위가 나온다. used를 빼지 않으면 두 번째 슬롯이 첫 번째와 동일한 소스를 고른
    뒤 build_layout의 중복 제거에 걸려 조용히 사라진다.

    다만 정말로 두 슬롯이 같은 카메라를 가리키는 상황(예: 낙탄 지점이 곧 발언
    현장인 경우)도 있다. 그럴 땐 used를 뺀 나머지 후보 중 발언과 관련된 게
    하나도 없다 — 이때는 억지로 엉뚱한 카메라를 채우지 말고 None을 돌려줘서
    이 슬롯은 그냥 비워두고 상시 표출 화면이 대신 채우게 한다.
    """
    used = used or set()
    pool = [c for c in candidates if c["id"] not in used]
    excluded_something = len(pool) < len(candidates)
    if not pool:
        return None
    if not utterance:
        return pool[0]
    mentioned_dirs = sources.mentioned_directions(utterance)
    scored = [
        (sources.text_score(source, utterance, mentioned_dirs), source["id"], source)
        for source in pool
    ]
    scored.sort(key=lambda x: (-x[0], x[1]))
    best_score, _, best_source = scored[0]
    if best_score > 0:
        return best_source
    if excluded_something:
        return None  # 관련 있는 다음 카메라가 없다 = 진짜 같은 카메라를 가리키는 것.
    return pool[0]  # 처음 고르는 슬롯이면 단서가 없어도 뭔가는 보여준다.


def _best_matches(
    candidates: list[dict], utterance: str, used: set[str] | None, max_count: int
) -> list[dict]:
    """관련도 높은 순으로 최대 max_count개를 고르되, 언급된 장소마다 먼저 하나씩 배정한다.

    "탄약고 부근, 발전소 부근"처럼 서로 다른 장소가 같이 언급되면, 단순히
    점수 1등부터 max_count개를 뽑을 경우 카메라가 많은 장소(예: 탄약고
    7대) 하나가 점수 동점자 목록을 다 차지해서 다른 장소(발전소 3대)는
    하나도 못 뜨는 문제가 생긴다. 그래서 먼저 발언에 나온 장소 순서대로
    "그 장소 이름이 명칭에 들어간 카메라 중 1등"을 하나씩 배정하고, 남는
    자리만 전체 후보 중 점수 순으로 채운다.

    남는 자리를 채울 때도 같은 지역(sources.region_of)에서 두 번째 카메라를
    또 뽑지 않는다. 탄약고 하나에 카메라가 7대 있으면 태그가 전부 겹쳐서
    점수 상위 N개가 전부 탄약고 카메라로 채워지기 쉽다 — 그러면 정작 다른
    지역 상황은 화면에 하나도 안 뜬다. 지역별로 최대 1대까지만 허용하고,
    그래도 자리가 남으면(=서로 다른 지역 후보가 다 떨어지면) 지역 제한을
    풀고 채운다.
    """
    running_used = set(used or ())
    picked: list[dict] = []
    used_regions: set[str] = set()

    for location in sources.mentioned_locations(utterance):
        if len(picked) >= max_count:
            break
        loc_pool = [c for c in candidates if location in c["name"]]
        match = _best_match(loc_pool, utterance, running_used)
        if match is not None:
            picked.append(match)
            running_used.add(match["id"])
            used_regions.add(sources.region_of(match))

    while len(picked) < max_count:
        pool = [c for c in candidates if sources.region_of(c) not in used_regions]
        match = _best_match(pool, utterance, running_used)
        if match is None:
            match = _best_match(candidates, utterance, running_used)
        if match is None:
            break
        picked.append(match)
        running_used.add(match["id"])
        used_regions.add(sources.region_of(match))

    return picked


def resolve_slot(slot_name: str, utterance: str, used: set[str] | None = None) -> list[dict]:
    """슬롯 이름 하나를 실제 화면 소스 목록으로 바꾼다. 못 찾으면 빈 목록.

    슬롯 정의에 "max"가 있으면(기본값 1) 관련 있는 소스를 그 개수까지 담아
    여러 타일에 나눠 띄운다. used는 이미 다른 슬롯이 골라 쓴 source_id
    목록이다. 방향/키워드로 후보를 직접 검색하는 슬롯(prefix/group/
    nearest_cctv)에만 적용한다 — fixed 슬롯은 대체할 다른 후보가 없으므로
    그대로 둔다.
    """
    spec = load_playbook()["slots"].get(slot_name)
    catalog = sources.load_catalog()

    if spec is None:
        # 플레이북에 정의가 없으면 소스 ID나 명칭으로 직접 지정한 것으로 본다.
        if sources.exists(slot_name):
            return [sources.by_id()[slot_name]]
        for source in catalog:
            if source["name"] == slot_name:
                return [source]
        return []

    kind = spec.get("type")

    if kind == "fixed":
        source = sources.by_id().get(spec.get("source_id", ""))
        return [source] if source else []

    max_count = max(int(spec.get("max", 1) or 1), 1)

    if kind == "prefix":
        prefix = spec.get("prefix", "")
        pool = [s for s in catalog if s["id"].startswith(prefix)]
        return _best_matches(pool, utterance, used, max_count)

    if kind == "group":
        pool = [
            s for s in catalog
            if any(s["id"].startswith(p) for p in spec.get("prefixes", []))
        ]
        return _best_matches(pool, utterance, used, max_count)

    if kind == "nearest_cctv":
        pool = [s for s in catalog if s["feed_type"] in ("CCTV", "열상", "바디캠", "이동형")]
        return _best_matches(pool, utterance, used, max_count)

    return []


def build_layout(situation_name: str, utterance: str = "") -> tuple[list[dict], list[str]]:
    """상황 유형에서 COP 레이아웃을 만든다.

    반환: (레이아웃, 해석 실패한 슬롯 이름들)
    """
    situation = find_situation(situation_name)
    if situation is None:
        return [], []

    layout: list[dict] = []
    unresolved: list[str] = []
    used: set[str] = set()

    def _append(source: dict, slot_name: str, origin: str) -> None:
        used.add(source["id"])
        layout.append(
            {
                "source_id": source["id"],
                "name": source["name"],
                "cell": source["cell"],
                "priority": len(layout) + 1,
                "slot": slot_name,
                "origin": origin,
            }
        )

    # 비행단 전장상황도는 상황 유형과 무관하게 항상 1순위(가장 큰 자리)로 고정 배치한다.
    # 어떤 CCTV가 지금 화면에 떠 있는지 한눈에 보여주는 기준 화면이기 때문이다.
    pinned_slot = load_playbook().get("pinned_slot", "")
    if pinned_slot:
        for source in resolve_slot(pinned_slot, utterance, used):
            _append(source, pinned_slot, "고정")

    for slot_name in situation.get("screens", []):
        resolved = resolve_slot(slot_name, utterance, used)
        if not resolved:
            unresolved.append(slot_name)
            continue
        for source in resolved:
            if source["id"] in used:
                continue  # fixed 슬롯이 이미 쓰인 소스를 가리키는 경우만 여기 걸린다.
            if len(layout) >= MAX_PANELS:
                break
            _append(source, slot_name, "상황")
        if len(layout) >= MAX_PANELS:
            break

    # --- 빈 자리 채우기 ---
    # 지휘소 대형 화면에 빈 칸이 남아 있으면 안 된다. 상황별 화면을 배치하고 남은 자리는
    # 상시 표출 목록 → 발언과 가장 관련 있는 CCTV 순으로 채운다.
    for slot_name in load_playbook().get("always_on", []):
        if len(layout) >= MAX_PANELS:
            break
        for source in resolve_slot(slot_name, utterance, used):
            if len(layout) >= MAX_PANELS:
                break
            if source["id"] not in used:
                _append(source, slot_name, "상시")

    # 배치가 확정된 뒤에 자리 크기를 다시 계산한다. 화면 수에 맞춰 2행 6열을
    # 빈틈없이 나눠 갖되, 우선순위가 높을수록 큰 자리를 차지한다.
    slots = tiling_for(len(layout))
    for item, slot in zip(layout, slots):
        item["grid"] = slot
        item["position"] = position_label(slot)

    return layout, unresolved


# ---------- UI 편집용 ----------


def to_table() -> list[dict]:
    """편집 화면에 쓸 표 형태. 사용자가 준 원본 표와 같은 모양이다."""
    rows = []
    for situation in load_playbook()["situations"]:
        row = {
            "상황 유형": situation["name"],
            "키워드": ", ".join(situation.get("keywords", [])),
        }
        for i in range(5):
            screens = situation.get("screens", [])
            row[f"{i + 1}순위"] = screens[i] if i < len(screens) else ""
        rows.append(row)
    return rows


def from_table(rows: list[dict]) -> dict:
    """편집된 표를 플레이북 구조로 되돌린다. slots 정의는 그대로 보존한다."""
    data = json.loads(json.dumps(load_playbook()))  # 깊은 복사
    situations = []
    for row in rows:
        name = str(row.get("상황 유형", "") or "").strip()
        if not name:
            continue
        screens = [
            str(row.get(f"{i + 1}순위", "") or "").strip()
            for i in range(5)
        ]
        keywords = [
            k.strip() for k in str(row.get("키워드", "") or "").split(",") if k.strip()
        ]
        situations.append(
            {"name": name, "keywords": keywords, "screens": [s for s in screens if s]}
        )
    data["situations"] = situations
    return data


def validate_table(rows: list[dict]) -> list[str]:
    """저장 전 점검. 해석할 수 없는 슬롯 이름을 알려준다."""
    problems = []
    for row in rows:
        name = str(row.get("상황 유형", "") or "").strip()
        if not name:
            continue
        for i in range(5):
            slot = str(row.get(f"{i + 1}순위", "") or "").strip()
            if slot and not resolve_slot(slot, ""):
                problems.append(f"'{name}' {i + 1}순위 — 알 수 없는 화면: {slot}")
    return problems

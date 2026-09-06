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

# FAST가 "이번 발언은 상황을 바꾸지 않는다"고 말할 때 쓰는 값. 플레이북의 상황 유형이
# 아니라 신호이며, 잡담·질문·화면 배치 지시처럼 문장 자체에 상황 단서가 없는 발언에 쓴다.
# 앱(context_memory)·데이터 생성(gen_dataset)·평가(evaluate)가 모두 이 상수를 쓴다 —
# 셋 중 하나라도 다른 문자열을 쓰면 조용히 어긋난다. 여기에 두는 이유는 이 모듈이
# streamlit에 의존하지 않아 학습·평가 환경에서도 import할 수 있기 때문이다.
KEEP_SITUATION = "유지"


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


# 우선순위별 고정 배치표 (2행 6열). 값은 (행, 열, 행병합, 열병합)이며 목록 순서가
# 곧 우선순위다. 어느 항목이든 면적이 앞 항목보다 커지지 않도록 손으로 맞춰 뒀다.
#
# 계산식으로 나누던 것을 표로 바꾼 이유:
#   예전에는 남은 패널을 위/아래 두 줄로 갈라 각 줄 안에서만 폭을 나눴다. 그러면
#   아랫줄 첫 패널이 윗줄 패널보다 넓어지는 일이 생겼다 — 8개를 띄우면 6순위(상시
#   표출용 보충 화면)가 2칸인데 2~5순위(그 상황에 실제로 필요한 화면)는 1칸이었다.
#   중요도·긴급도에 따라 크기를 정한다는 전제가 거기서 무너진다. 배치 경우의 수가
#   12가지뿐이므로 계산으로 맞추기보다 표로 고정하는 편이 검증도 쉽다.
_TILINGS: dict[int, list[tuple[int, int, int, int]]] = {
    1: [(1, 1, 2, 6)],
    2: [(1, 1, 2, 4), (1, 5, 2, 2)],
    3: [(1, 1, 2, 3), (1, 4, 2, 2), (1, 6, 2, 1)],
    4: [(1, 1, 2, 3), (1, 4, 1, 3), (2, 4, 1, 2), (2, 6, 1, 1)],
    5: [(1, 1, 2, 2), (1, 3, 1, 2), (1, 5, 1, 2), (2, 3, 1, 2), (2, 5, 1, 2)],
    6: [(1, 1, 2, 2), (1, 3, 1, 2), (1, 5, 1, 2), (2, 3, 1, 2), (2, 5, 1, 1), (2, 6, 1, 1)],
    7: [(1, 1, 2, 2), (1, 3, 1, 2), (1, 5, 1, 2),
        (2, 3, 1, 1), (2, 4, 1, 1), (2, 5, 1, 1), (2, 6, 1, 1)],
    8: [(1, 1, 2, 2), (1, 3, 1, 2), (1, 5, 1, 1), (1, 6, 1, 1),
        (2, 3, 1, 1), (2, 4, 1, 1), (2, 5, 1, 1), (2, 6, 1, 1)],
    9: [(1, 1, 2, 2), (1, 3, 1, 1), (1, 4, 1, 1), (1, 5, 1, 1), (1, 6, 1, 1),
        (2, 3, 1, 1), (2, 4, 1, 1), (2, 5, 1, 1), (2, 6, 1, 1)],
}


def tiling_for(n: int) -> list[tuple[int, int, int, int]]:
    """패널 n개를 2행 6열에 빈틈없이 배치한다.

    개수에 따라 자리를 다시 잡으므로 화면이 2개든 9개든 벽에 빈칸이 남지 않는다.
    우선순위가 높을수록 넓은 자리를 갖고, 1순위는 위아래를 관통하는 대형 화면이 된다.
    반환값은 (행, 열, 행병합, 열병합) 목록이며 우선순위 순서와 일치한다.
    """
    if n <= 0:
        return []
    n = min(n, MAX_PANELS)
    if n in _TILINGS:
        return list(_TILINGS[n])

    # 10개 이상은 12칸에 한 칸짜리가 대부분이라 크기로 중요도를 드러낼 수 없다.
    # 아랫줄은 여섯 칸을 하나씩 채우고, 남는 열은 윗줄 상위 화면에 몰아줘
    # 최소한 하위 화면이 상위보다 커지는 역전만은 막는다.
    top_n = n - GRID_COLS
    weights = [1.0 / (i + 1) ** 0.7 for i in range(top_n)]
    slots: list[tuple[int, int, int, int]] = []
    col = 1
    for w in _distribute(GRID_COLS, weights):
        slots.append((1, col, 1, w))
        col += w
    slots.extend((2, c, 1, 1) for c in range(1, GRID_COLS + 1))
    return slots


def layout_from_source_ids(source_ids: list) -> tuple[list[dict], list[str]]:
    """모델이 낸 source_id 목록을 실제 레이아웃으로 바꾼다.

    모델이 화면 구성을 직접 낼 때(기획서 원안 구조) 그 출력을 화면에 올리기 전에
    반드시 거쳐야 하는 검증 계층이다. 모델은 다음 세 가지를 낼 수 있고, 그대로 두면
    화면이 깨진다.
      · 카탈로그에 없는 이름 — 존재하지 않는 화면이라 그 자리가 빈다.
      · 같은 화면 중복 — 벽면의 두 칸이 같은 것을 비춘다.
      · 개수 초과 — 12칸을 넘는다.
    격자 좌표는 여기서 tiling_for로 계산한다. 격자 채우기는 판단이 아니라 패널 개수와
    순번만으로 정해지는 기하학이고, 좌표까지 모델이 내면 겹침·빈칸을 어떤 학습으로도
    구조적으로는 막지 못하기 때문이다.

    반환: (레이아웃, 버려진 id 목록)
    """
    catalog = sources.by_id()
    valid: list[dict] = []
    invalid: list[str] = []
    seen: set[str] = set()
    for raw in source_ids:
        sid = str(raw).strip()
        entry = catalog.get(sid)
        if entry is None or sid in seen:
            invalid.append(sid)
            continue
        seen.add(sid)
        valid.append({
            "source_id": sid,
            "name": entry["name"],
            "cell": entry["cell"],
            "slot": "모델 판단",
            "origin": "모델",
        })

    valid = valid[:MAX_PANELS]
    slots = tiling_for(len(valid))
    layout = []
    for i, (item, slot) in enumerate(zip(valid, slots)):
        layout.append({**item, "priority": i + 1, "grid": slot,
                       "position": position_label(slot)})
    return layout, invalid


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


DEFAULT_PANEL_BUDGET = 6


def panel_budget() -> int:
    """상시 표출 화면까지 포함해 한 번에 띄울 화면 수의 상한.

    플레이북의 max_panels로 운용자가 조정한다(없으면 6). 상황별 화면이 이보다
    많으면 그건 운용자가 명시한 것이므로 자르지 않고, 상시 보충만 여기서 멈춘다.
    """
    raw = load_playbook().get("max_panels", DEFAULT_PANEL_BUDGET)
    try:
        return max(1, min(MAX_PANELS, int(raw)))
    except (TypeError, ValueError):
        return DEFAULT_PANEL_BUDGET


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
    """상황 유형 하나에서 COP 레이아웃을 만든다.

    반환: (레이아웃, 해석 실패한 슬롯 이름들)
    """
    return build_layout_multi([situation_name], utterance)


def build_layout_multi(
    situation_names: list[str], utterance: str = ""
) -> tuple[list[dict], list[str]]:
    """동시에 진행 중인 여러 상황의 화면을 한 벽면에 함께 배치한다.

    두 사태가 같이 진행 중인데(예: 무인기 접근 + 지상 침투) 화면이 한 상황 것만
    보이면, 나머지 사태는 지휘관 시야에서 그대로 사라진다. 실제로 그런 제보가 있었다
    — 하나가 확정되는 순간 다른 하나가 화면에서 없어졌다.

    병합 방식은 "라운드로빈"이다. 상황 A의 1순위 화면, 상황 B의 1순위 화면, A의 2순위,
    B의 2순위… 순으로 번갈아 채운다. 앞쪽 상황을 다 채우고 남는 자리에 뒤 상황을 넣으면
    두 번째 상황은 항상 구석의 한 칸짜리로 밀려나므로, 각 상황의 가장 중요한 화면이
    먼저 큰 자리를 잡게 한다.

    situation_names는 최근에 갱신된 순서(앞이 가장 최근)로 받는다.
    """
    situations = [s for s in (find_situation(n) for n in situation_names) if s]
    if not situations:
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

    # 상황별 화면을 라운드로빈으로 섞는다. 상황이 하나뿐이면 예전과 완전히 같은
    # 순서가 된다(그 상황의 screens를 앞에서부터 그대로 도는 것과 같다).
    #
    # 상황이 둘 이상이면 화면 수를 panel_budget까지로 자른다. 안 자르면 두 상황의
    # 화면이 다 들어와 최대 10개가 되고, 그러면 벽면 12칸 중 8칸이 한 칸짜리가 되어
    # "중요도에 따라 크기가 달라진다"는 전제가 무너진다. 라운드로빈이라 잘리는 것은
    # 각 상황의 하위 화면이고, 두 상황의 상위 화면은 모두 살아남는다.
    # 상황이 하나면 예전 규칙 그대로다 — 운용자가 플레이북에 직접 적어 넣은 화면은
    # 그 자체가 의도이므로 budget을 넘더라도 자르지 않는다.
    screen_cap = MAX_PANELS if len(situations) == 1 else panel_budget()
    screen_lists = [situation.get("screens", []) for situation in situations]
    for rank in range(max((len(x) for x in screen_lists), default=0)):
        for screens in screen_lists:
            if rank >= len(screens) or len(layout) >= screen_cap:
                continue
            slot_name = screens[rank]
            resolved = resolve_slot(slot_name, utterance, used)
            if not resolved:
                if slot_name not in unresolved:
                    unresolved.append(slot_name)
                continue
            for source in resolved:
                if source["id"] in used:
                    continue  # fixed 슬롯이 이미 쓰인 소스를 가리키는 경우만 여기 걸린다.
                if len(layout) >= screen_cap:
                    break
                _append(source, slot_name, "상황")
        if len(layout) >= screen_cap:
            break

    # --- 상시 표출 화면으로 보충 ---
    # 상황별 화면만으로는 화면 수가 적을 수 있어 상시 표출 목록으로 채운다. 다만
    # 벽면 12칸을 전부 다른 화면으로 채우면 안 된다 — 그러면 대부분이 한 칸짜리가
    # 되어 중요도에 따라 크기를 다르게 준다는 전제가 사라지고, 정작 그 상황에 필요한
    # 화면이 상시 보충 화면에 묻힌다. panel_budget까지만 보충하고, 남는 칸은
    # tiling_for가 상위 화면을 키워서 메운다(빈 칸은 여전히 생기지 않는다).
    budget = panel_budget()
    for slot_name in load_playbook().get("always_on", []):
        if len(layout) >= budget:
            break
        for source in resolve_slot(slot_name, utterance, used):
            if len(layout) >= budget:
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

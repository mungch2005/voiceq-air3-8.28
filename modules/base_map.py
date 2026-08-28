"""가상 비행단 배치도 — 고정된 기지 격자(A1~J7) 좌표계.

지도는 상황에 따라 바뀌지 않는 고정 배치도다(map_renderer.py가 그린다). LLM은
이 좌표계를 전혀 다루지 않는다 — "어느 CCTV가 관련 있는지"는 발언 텍스트와
카메라 태그를 코드가 직접 비교해서 정한다(sources.text_score, playbook.py).
이 모듈은 그 고정 배치도를 그리는 데 필요한 격자 좌표 변환만 담당한다.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

BASE_MAP_PATH = Path(__file__).resolve().parent.parent / "data" / "base_map.json"


@lru_cache(maxsize=1)
def load_base_map() -> dict:
    return json.loads(BASE_MAP_PATH.read_text(encoding="utf-8"))


def cell_to_index(cell: str) -> tuple[int, int] | None:
    """'C4' -> (col_index=2, row_index=3). 잘못된 값이면 None."""
    if not cell or len(cell) < 2:
        return None
    base = load_base_map()
    cols = base["base"]["grid"]["cols"]
    col_char = cell[0].upper()
    if col_char not in cols:
        return None
    try:
        row = int(cell[1:])
    except ValueError:
        return None
    if not (1 <= row <= base["base"]["grid"]["rows"]):
        return None
    return cols.index(col_char), row - 1


def cell_distance(cell_a: str, cell_b: str) -> float | None:
    """두 격자 간 거리(칸 단위)."""
    a, b = cell_to_index(cell_a), cell_to_index(cell_b)
    if a is None or b is None:
        return None
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5

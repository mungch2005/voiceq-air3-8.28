"""전장상황도 자동 배치용 프리셋 아이콘 — 운용자가 표에서 직접 추가/수정한다.

프리셋 하나는 (이름, 이모지, 색상, 키워드)로 이뤄진다. 발언 텍스트에 키워드가
하나라도 들어 있으면 그 아이콘을 자동으로 배치한다(context_memory._auto_place_markers).
상황 유형 분류와는 무관하게 발언 내용 자체로 판단하므로, 같은 발언에 여러 프리셋이
동시에 걸릴 수 있다 — 예: "무인기 대응으로 전술차량 배치"는 무인기·지상 차량 둘 다 켠다.
"""

from __future__ import annotations

import json
from pathlib import Path

PRESETS_PATH = Path(__file__).resolve().parent.parent / "data" / "map_icon_presets.json"

_cache: dict | None = None


def load_presets(force: bool = False) -> list[dict]:
    global _cache
    if _cache is None or force:
        _cache = json.loads(PRESETS_PATH.read_text(encoding="utf-8"))
    return _cache["presets"]


def save_presets(presets: list[dict]) -> None:
    global _cache
    data = {"presets": presets}
    PRESETS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _cache = data


def find_preset(label: str) -> dict | None:
    return next((p for p in load_presets() if p.get("label") == label), None)


def matches(preset: dict, text: str) -> bool:
    """발언 텍스트에 이 프리셋의 키워드가 하나라도 들어 있는지."""
    return any(kw and kw in text for kw in preset.get("keywords", []))


def to_table() -> list[dict]:
    """편집 화면에 쓸 표 형태."""
    return [
        {
            "이름": p.get("label", ""),
            "아이콘": p.get("emoji", ""),
            "색상": p.get("color", "#3D5A80"),
            "키워드": ", ".join(p.get("keywords", [])),
        }
        for p in load_presets()
    ]


def from_table(rows: list[dict]) -> list[dict]:
    """편집된 표를 프리셋 목록으로 되돌린다. 이름이 빈 행은 버린다."""
    presets = []
    for row in rows:
        label = str(row.get("이름", "") or "").strip()
        if not label:
            continue
        keywords = [
            k.strip() for k in str(row.get("키워드", "") or "").split(",") if k.strip()
        ]
        presets.append(
            {
                "label": label,
                "emoji": str(row.get("아이콘", "") or "").strip() or "📍",
                "color": str(row.get("색상", "") or "").strip() or "#3D5A80",
                "keywords": keywords,
            }
        )
    return presets

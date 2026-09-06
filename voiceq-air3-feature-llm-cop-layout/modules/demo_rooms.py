"""시연 페이지의 방 배치 — 화자를 전투지휘소/상황실 중 한 곳에 배정한다.

발언한 사람의 말풍선을 어느 방에 띄울지 정하기 위한 것이다. 배정 규칙은 두 단계다.
  ① demo_rooms.json의 seating에 이름이 있으면 그대로 따른다 (명시 배치).
  ② 없으면 organization.json에서 화자의 소속을 전대 레벨까지 거슬러 올라가,
     같은 unit을 가진 방에 넣는다.
어느 쪽에도 안 걸리면 전투지휘소로 보낸다 — 화자가 화면에서 사라지는 것보다
지휘소에 한 명 더 앉는 편이 낫다.

편제(organization.json)를 건드리지 않는다. 그 파일은 본 앱의 화자 드롭다운과
LLM 프롬프트가 함께 쓰므로, 시연용 좌석 배치 때문에 바꾸면 본 앱의 판단이 달라진다.
"""

from __future__ import annotations

import json
from pathlib import Path

from modules import organization as org

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "demo_rooms.json"

_cache: dict | None = None


def load() -> dict:
    global _cache
    if _cache is None:
        _cache = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    return _cache


def rooms() -> list[dict]:
    return load()["rooms"]


def room_ids() -> list[str]:
    return [r["id"] for r in rooms()]


def situation_rooms() -> list[dict]:
    """전투지휘소를 뺀 상황실들. 화면 우측 2×2 카드에 이 순서로 놓는다."""
    return [r for r in rooms() if r.get("kind") != "cp"]


def cp_room() -> dict:
    return next(r for r in rooms() if r.get("kind") == "cp")


def is_system(title: str) -> bool:
    """사람이 아닌 화자(지휘통제망 채팅 등). 좌석 대신 티커로 표시한다."""
    return title in load().get("system_speakers", [])


def _unit_chain(unit_id: str) -> list[str]:
    """소속 unit에서 최상위까지의 사슬. ['MP', 'SEC', 'WING'] 같은 형태."""
    units = {u["id"]: u for u in org.load_org()["units"]}
    chain: list[str] = []
    current = unit_id
    while current and current not in chain:
        chain.append(current)
        current = units.get(current, {}).get("parent")
    return chain


def room_of(title: str) -> str:
    """화자가 있는 방의 id. 모르는 이름은 전투지휘소로 보낸다."""
    data = load()
    seated = data.get("seating", {}).get(title)
    if seated:
        return seated

    info = org.lookup(title)
    if info:
        by_unit = {r["unit"]: r["id"] for r in rooms() if r.get("unit")}
        for unit_id in _unit_chain(info.get("unit", "")):
            if unit_id in by_unit:
                return by_unit[unit_id]
    return cp_room()["id"]


def occupants(room_id: str) -> list[str]:
    """그 방에 있는 화자들. 전투지휘소는 상석(단장·부단장)을 앞에 둔다.

    체계 화자(지휘통제망 채팅)는 좌석을 차지하지 않으므로 빠진다.
    """
    data = load()
    people = [t for t in org.speaker_titles() if not is_system(t) and room_of(t) == room_id]
    if room_id != cp_room()["id"]:
        return people

    head = [t for t in data.get("head_seats", []) if t in people]
    return head + [t for t in people if t not in head]


def unit_badges(room_id: str) -> list[str]:
    """방을 대표하는 예하 부대 이름들.

    화자가 배정되지 않은 방(예: 정비 상황실 — 편제상 정비 쪽 대대장이 화자 목록에
    없다)도 빈 카드로 두지 않기 위한 것이다. 방이 무엇을 담당하는지는 보여야 한다.
    """
    room = next((r for r in rooms() if r["id"] == room_id), None)
    if not room or not room.get("unit"):
        return []
    return [
        u["name"]
        for u in org.load_org()["units"]
        if u.get("parent") == room["unit"]
    ]


def seating_report() -> dict[str, list[str]]:
    """방별 배정 결과. 배정이 의도대로 됐는지 확인하는 용도."""
    return {r["id"]: occupants(r["id"]) for r in rooms()}

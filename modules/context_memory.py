"""Context Memory / 작전상황일지 상태 관리 (세션 중에는 st.session_state, 필요 시 JSON 파일 영속화)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

from modules import llm_engine as engine
from modules import map_icons as mi
from modules import map_renderer as mr
from modules import playbook as pb
from modules import sources

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONTEXT_MEMORY_PATH = DATA_DIR / "context_memory.json"
OPERATION_LOG_PATH = DATA_DIR / "operation_log.json"

KST = timezone(timedelta(hours=9))


def now_kst() -> str:
    """서버 시간대와 무관하게 한국 시간(KST) 기준 HH:MM:SS를 반환한다."""
    return datetime.now(KST).strftime("%H:%M:%S")


def init_session_state() -> None:
    defaults = {
        "context_memory_summary": "",
        "user_corrections": [],
        "utterance_log": [],
        "cop_layout": [],
        "situation_board": [],
        "operation_log": [],
        "map_markers": [],
        "latency_history": [],
        "display_latency_history": [],
        "dropped_sources": [],
        "situation_type": "",
        "situation_reason": "",
        "situation_unmatched": "",
        "voice_transcript": "",
        "provider": engine.configured_provider(),
        "selected_model": engine.default_model_for(engine.configured_provider()),
        "model_options": [],
        # 파인튜닝 모델(few-shot 없이 학습)을 쓸 때만 켠다. 기본 모델명을 보고 정한다.
        "skip_few_shot": engine.is_finetuned(
            engine.default_model_for(engine.configured_provider())
        ),
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def apply_fast_result(result_data: dict, utterance: str = "") -> None:
    """표출 경로 결과 — 상황 유형을 받아 플레이북으로 화면을 구성한다.

    LLM은 상황 유형 분류만 한다. 어떤 화면을 어느 순서로 띄울지는 운용자가 만든
    플레이북이 결정하고, "해당 지역 CCTV" 같은 위치 기반 슬롯은 이번 발언 텍스트를
    코드가 직접 읽어(playbook.resolve_slot) 방위·시설명이 겹치는 카메라를 고른다 —
    모델이 좌표나 화면 이름을 지어낼 여지가 없다.
    """
    situation = result_data.get("situation") or {}
    raw_type = str(situation.get("type", "") or "").strip()

    matched = pb.find_situation(raw_type)
    resolved_name = matched["name"] if matched else "기타 상황"

    layout, unresolved = pb.build_layout(resolved_name, utterance)
    st.session_state.cop_layout = layout
    st.session_state.dropped_sources = unresolved
    st.session_state.situation_type = resolved_name
    st.session_state.situation_reason = str(situation.get("reason", "") or "")
    st.session_state.situation_unmatched = raw_type if not matched else ""


def _resolve_location(utterance: str) -> tuple[tuple[float, float] | None, str]:
    """발언에서 지도 위치를 찾는다. 시설명이 언급됐으면 그 시설, 없으면 언급된
    방위를 담당하는 경계초소 위치를 근사치로 쓴다. 둘 다 없으면 (None, "") —
    모르는 위치를 지도에 억지로 찍지 않는다(map_renderer.py와 같은 원칙).
    """
    locations = sources.mentioned_locations(utterance)
    if locations:
        pos = mr.facility_center(locations[0])
        if pos:
            return pos, locations[0]

    for direction in sources.mentioned_directions(utterance):
        anchor = mr.direction_anchor(direction)
        if anchor:
            name, x, y = anchor
            return (x, y), name

    return None, ""


def _auto_place_markers(event_id: str, utterance: str) -> None:
    """발언 텍스트에 프리셋 키워드가 들어 있으면 해당 아이콘을 자동으로 놓거나 옮긴다.

    마커는 (그룹 키, 프리셋 이름) 조합으로 구분한다. 그룹 키는 가능하면 사태
    event_id를 쓴다 — event_id로 구분하지 않고 프리셋 이름만으로 구분하면,
    같은 종류의 사태가 동시에 둘 이상 진행 중일 때(예: 동쪽 무인기 3대·서쪽
    무인기 2대) 하나로 뭉개져 버린다. 한 사태 안에서도 대상(무인기)과 대응
    자산(전술차량)처럼 서로 다른 프리셋이 동시에 걸릴 수 있으므로 그 조합은
    각자 별도 마커로 둔다.

    이 발언이 작전상황일지에 기록될 만한 사태/조치로 분류되지 않아 event_id가
    없는 경우도 있다(예: 모델이 "무시"로 분류) — 그렇다고 지도 마커까지 안
    띄우면 안 된다. 위치·종류가 발언에 분명히 있는데 일지 분류 결과에 끌려가
    지도가 비게 되는 것을 막기 위해, event_id가 없으면 프리셋 이름만으로 된
    그룹 키를 대신 쓴다.

    같은 (그룹 키, 프리셋) 마커가 이미 떠 있으면 새로 만들지 않고 위치만 갱신한다
    — 그 사태가 전개되며 위치가 바뀌면(예: "무인기가 격납고로 이동") 새로
    쌓이지 않고 하나의 아이콘이 최신 위치로 옮겨가게 하기 위해서다.
    실무자는 '전장상황도 조작' 탭에서 이 자동 위치를 정밀하게 조정만 하면 된다.
    """
    matched_presets = [p for p in mi.load_presets() if mi.matches(p, utterance)]
    if not matched_presets:
        return

    pos, facility = _resolve_location(utterance)
    if pos is None:
        return

    markers = st.session_state.map_markers
    for preset in matched_presets:
        group_key = event_id or f"_standalone_{preset['label']}"
        marker = {
            "event_id": group_key, "x": pos[0], "y": pos[1],
            "emoji": preset["emoji"], "color": preset["color"], "label": preset["label"],
            "facility": facility, "timestamp": now_kst(),
        }
        existing = next(
            (m for m in markers
             if m.get("event_id") == group_key and m.get("label") == preset["label"]),
            None,
        )
        if existing:
            existing.update(marker)
        else:
            markers.append(marker)


def apply_full_result(
    result_data: dict, speaker: str = "", timestamp: str = "", utterance: str = ""
) -> None:
    """기록 경로 결과 — 누적 요약, 상황판, 작전상황일지, 전장상황도 아이콘을 갱신한다."""
    st.session_state.context_memory_summary = result_data.get("context_memory", "")
    st.session_state.situation_board = sorted(
        result_data.get("situation_board", []), key=lambda x: x.get("rank", 99)
    )

    event_id = _merge_operation_log_entry(
        result_data.get("operation_log_entry") or {}, speaker, timestamp
    )
    _auto_place_markers(event_id or "", utterance)


_EVENT_ID_RE = re.compile(r"^(?:사태|사건|상황|이벤트|event)\s*[-_]?\s*(\d+)$", re.IGNORECASE)


def _normalize_event_id(raw: object) -> str:
    """사태 ID 표기 흔들림을 흡수한다.

    모델이 "사태1" 대신 "사건2", "상황 3", "event-1" 같은 변형을 내는 경우가 있다.
    그대로 두면 같은 사태인데도 매칭에 실패해 새 사태가 생긴다. 번호가 같으면
    같은 사태로 본다.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    match = _EVENT_ID_RE.match(text)
    return f"사태{int(match.group(1))}" if match else text


def _merge_operation_log_entry(entry: dict, speaker: str, timestamp: str) -> str | None:
    """LLM의 3분류 결과(kind: 상황/조치/무시)를 작전상황일지에 반영한다.

    - 상황: 새 사태를 만든다. 발화 시간이 그 사태의 '시간' 칸이 된다.
    - 조치: event_id로 지목한 기존 사태의 타임라인에 이어 붙인다. 화자의 부서가
      '부서' 칸에, content가 '조치내용' 칸에 들어간다.
    - 무시(또는 알 수 없는 값): 일지를 건드리지 않는다.
    발언 하나가 곧 사태 하나가 되지 않도록 하는 것이 이 함수의 핵심이다.

    반환값은 실제로 기록에 쓰인 event_id다(모델이 지목한 ID가 아니라, 못 찾아서
    새로 발급했거나 재사용을 막고 새로 발급한 경우의 최종 ID). 전장상황도 아이콘
    자동 배치(_auto_place_markers)가 있으면 이 값으로 마커를 사태와 묶는다.
    무시했거나 내용이 없으면 None — 일지는 건드리지 않지만, 지도 마커는 별개로
    발언 텍스트만 보고 계속 자동 배치된다(일지 분류 실패가 지도까지 비우면 안 되므로).
    """
    kind = str(entry.get("kind", "") or "").strip()
    content = str(entry.get("content", "") or "").strip()
    if kind not in ("상황", "조치") or not content:
        return None

    log = st.session_state.operation_log
    event_id = _normalize_event_id(entry.get("event_id", ""))

    if kind == "조치":
        existing = next((e for e in log if e.get("event_id") == event_id), None) if event_id else None
        if existing is not None:
            existing["entries"].append(
                {"timestamp": timestamp, "speaker": speaker, "detail": content}
            )
            return event_id
        # 대상 사태를 못 찾았다 — 모델이 ID를 잘못 지목한 경우다. 조치 내용을 잃지
        # 않도록 새 사태로라도 남긴다.

    # kind == "상황"이거나, "조치"인데 대상을 못 찾은 경우: 새 사태로 기록한다.
    # 모델이 이미 쓰인 ID를 새 상황에 재사용하면 기존 사태를 덮어써 버리므로,
    # 그런 경우엔 다음 번호로 새로 발급한다.
    existing_ids = {e.get("event_id") for e in log}
    if not event_id or event_id in existing_ids:
        event_id = f"사태{len(log) + 1}"

    log.append(
        {
            "event_id": event_id,
            "title": content,
            "entries": [{"timestamp": timestamp, "speaker": speaker, "detail": content}],
        }
    )
    return event_id


def add_manual_correction(correction_text: str) -> None:
    st.session_state.user_corrections.append(correction_text)


def persist_to_disk() -> None:
    """데모 간 영속성이 필요할 때 로컬 JSON으로 저장 (선택 사항)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTEXT_MEMORY_PATH.write_text(
        json.dumps(
            {
                "summary": st.session_state.get("context_memory_summary", ""),
                "user_corrections": st.session_state.get("user_corrections", []),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    OPERATION_LOG_PATH.write_text(
        json.dumps(st.session_state.get("operation_log", []), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

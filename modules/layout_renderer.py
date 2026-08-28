"""COP 레이아웃 JSON -> Streamlit Video Wall 그리드 렌더링."""

from __future__ import annotations

import hashlib
import html

import streamlit as st

from modules import map_renderer as mr
from modules import organization as org
from modules import playbook as pb


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)

# 소스 이름 -> 플레이스홀더 색상 (실제 영상 피드 없이 색상 블록으로 시현)
_PALETTE = ["#264653", "#2A6F77", "#3D5A80", "#5C4D7D", "#7A4B6B", "#8A3033"]

POSITION_ORDER = ["좌측대형", "우측상단", "우측하단", "중앙"]


def _color_for(name: str) -> str:
    idx = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16) % len(_PALETTE)
    return _PALETTE[idx]


MAP_SOURCES = ("SYS-BASEMAP",)
SITBOARD_SOURCE_ID = "SYS-SITBOARD"

_URGENCY_COLOR = {"긴급": "#8A3033", "주의": "#B8860B", "관찰": "#3D5A80"}


def _situation_board_body(situation_board: list[dict]) -> str:
    """situation_board(사태 우선순위 목록)를 SYS-SITBOARD 타일 안에 "N순위" 카드로 쌓는다."""
    if not situation_board:
        return (
            '<div style="flex:1; display:flex; align-items:center; justify-content:center; '
            'padding:8px; text-align:center; font-size:0.7rem; opacity:0.5;">'
            "아직 판단된 사태가 없습니다.</div>"
        )

    cards = []
    for item in situation_board:
        rank = item.get("rank", "-")
        event = _esc(item.get("event", ""))
        urgency = str(item.get("urgency", "") or "")
        color = _URGENCY_COLOR.get(urgency, "#3D5A80")
        cards.append(
            f'<div style="background:rgba(255,255,255,0.05); border-left:3px solid {color}; '
            f'border-radius:4px; padding:5px 7px;">'
            f'<div style="font-size:0.62rem; opacity:0.65;">{_esc(rank)}순위 '
            f'<span style="padding:1px 6px; border-radius:8px; background:{color}; '
            f'margin-left:4px;">{_esc(urgency)}</span></div>'
            f'<div style="font-size:0.74rem; font-weight:700; margin-top:2px;">{event}</div>'
            "</div>"
        )

    return (
        '<div style="flex:1; display:flex; flex-direction:column; gap:5px; padding:7px; '
        'overflow:auto;">' + "".join(cards) + "</div>"
    )


def render_cop_wall(
    cop_layout: list[dict],
    situation_board: list[dict] | None = None,
    map_markers: list[dict] | None = None,
) -> None:
    """2행 6열 Video Wall. 1순위는 좌측 2×2 대형 화면을 차지한다.

    전장 상황도는 별도 탭이 아니라 이 안에서 실제 SVG로 인라인 표출하며, 상황과
    무관하게 항상 1순위 자리에 고정된다. 지도 위에는 지금 화면에 떠 있는 CCTV의
    위치만 표시한다 — 지휘소 대형 화면을 그대로 옮겨온 모습이어야 하기 때문이다.

    "작전상황판"(SYS-SITBOARD) 타일이 플레이북에 의해 화면에 뜨면, 그 타일
    안에는 회색 플레이스홀더 대신 situation_board(우선순위 판단 목록)를
    "N순위" 카드로 직접 그려 넣는다.
    """
    st.subheader("COP 화면 구성 — Video Wall 2×6")

    if not cop_layout:
        st.info("아직 화면 구성이 결정되지 않았습니다. 발언을 입력하면 자동으로 배치됩니다.")
        return

    panels = []
    for item in cop_layout[: pb.MAX_PANELS]:
        row, col, rspan, cspan = item.get("grid", (1, 1, 1, 1))
        panels.append(
            _panel_html(item, row, col, rspan, cspan, cop_layout, situation_board, map_markers)
        )

    st.markdown(
        f'<div style="display:grid; grid-template-columns:repeat({pb.GRID_COLS}, 1fr); '
        f'grid-template-rows:repeat({pb.GRID_ROWS}, minmax(150px, auto)); gap:8px; '
        f'background:#05080B; padding:10px; border-radius:8px; '
        f'border:1px solid rgba(255,255,255,0.08);">' + "".join(panels) + "</div>",
        unsafe_allow_html=True,
    )


def _panel_html(
    item: dict,
    row: int,
    col: int,
    rspan: int,
    cspan: int,
    cop_layout: list[dict],
    situation_board: list[dict] | None = None,
    map_markers: list[dict] | None = None,
) -> str:
    source_id = item.get("source_id", "")
    name = item.get("name", source_id)
    priority = item.get("priority", "-")
    slot = item.get("slot", "")
    is_map = source_id in MAP_SOURCES
    is_board = source_id == SITBOARD_SOURCE_ID

    header = (
        f'<div style="display:flex; justify-content:space-between; align-items:center; '
        f'gap:6px; padding:5px 8px; background:rgba(0,0,0,0.45); font-size:0.72rem;">'
        f'<span style="font-weight:700; color:#E6EDF3; overflow:hidden; '
        f'text-overflow:ellipsis; white-space:nowrap;">{priority}. {_esc(name)}</span>'
        f'<span style="font-family:monospace; opacity:0.5; white-space:nowrap;">'
        f"{_esc(source_id)}</span></div>"
    )

    if is_map:
        active = [x for x in cop_layout if x.get("source_id") not in MAP_SOURCES]
        body = (
            f'<div style="flex:1; padding:6px; overflow:hidden;">'
            f"{mr.build_map_svg(active, compact=(cspan < 2), markers=map_markers)}</div>"
        )
        bg = "#0B0F14"
    elif is_board:
        body = _situation_board_body(situation_board or [])
        bg = "#0B0F14"
    else:
        color = _color_for(source_id)
        body = (
            f'<div style="flex:1; display:flex; flex-direction:column; align-items:center; '
            f'justify-content:center; gap:5px; padding:8px; text-align:center;">'
            f'<div style="font-size:0.7rem; opacity:0.6;">격자 {_esc(item.get("cell", ""))}</div>'
            f'<div style="font-size:0.65rem; opacity:0.4;">실제 피드 연동 시 표출</div></div>'
        )
        bg = color

    footer = (
        f'<div style="padding:3px 8px; background:rgba(0,0,0,0.35); font-size:0.62rem; '
        f'opacity:0.55; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;">'
        f"플레이북 슬롯: {_esc(slot)}</div>"
        if slot else ""
    )

    return (
        f'<div style="grid-row:{row} / span {rspan}; grid-column:{col} / span {cspan}; '
        f'background:{bg}; border:1px solid rgba(255,255,255,0.14); border-radius:6px; '
        f'overflow:hidden; display:flex; flex-direction:column; min-height:150px;">'
        f"{header}{body}{footer}</div>"
    )


_CELL_STYLE = "padding:6px 10px; border:1px solid rgba(255,255,255,0.12); vertical-align:top; font-size:0.85rem;"
_HEAD_STYLE = _CELL_STYLE + " background:rgba(255,255,255,0.06); font-weight:700;"


def _hhmm(timestamp: str) -> str:
    """"HH:MM:SS" 등으로 기록된 시각을 24시간제 "HH:MM"으로 자른다."""
    parts = str(timestamp or "").split(":")
    return f"{parts[0]}:{parts[1]}" if len(parts) >= 2 else str(timestamp or "")


def _log_row_groups(operation_log: list[dict]) -> list[tuple[dict, int]]:
    """사태별 entries를 (표시행, 그 사태가 차지하는 행 수) 목록으로 펼친다.

    한 사태에 여러 부서가 각각 조치했으면 부서 수만큼 행이 나뉜다. '시간'은
    부서마다 보고 순간이 달라도 그 사태를 최초로 기록한 시각(entries[0])
    하나로 고정한다 — 표에서 같은 사태의 시간·상황 열을 병합해서 보여주기
    위해서다. 최신 사태가 위로 오도록 역순으로 돈다.
    """
    groups: list[tuple[dict, int]] = []
    for event in reversed(operation_log):
        entries = event.get("entries", [])
        if not entries:
            continue
        event_time = _hhmm(entries[0].get("timestamp", ""))
        situation = event.get("title", "") or event.get("event_id", "")
        span = len(entries)
        for entry in entries:
            groups.append(
                (
                    {
                        "시간": event_time,
                        "상황": situation,
                        "부서": org.department_abbr(entry.get("speaker", "")),
                        "조치내용": entry.get("detail", ""),
                    },
                    span,
                )
            )
    return groups


def operation_log_rows(operation_log: list[dict]) -> list[dict]:
    """CSV 내보내기용 평평한 표. 시간·상황 열이 같은 사태 안에서는 행마다 반복된다."""
    return [row for row, _ in _log_row_groups(operation_log)]


def _log_table_html(operation_log: list[dict]) -> str:
    groups = _log_row_groups(operation_log)
    body = []
    i = 0
    while i < len(groups):
        row, span = groups[i]
        body.append(
            "<tr>"
            f'<td rowspan="{span}" style="{_CELL_STYLE}">{_esc(row["시간"])}</td>'
            f'<td rowspan="{span}" style="{_CELL_STYLE}">{_esc(row["상황"])}</td>'
            f'<td style="{_CELL_STYLE}">{_esc(row["부서"])}</td>'
            f'<td style="{_CELL_STYLE}">{_esc(row["조치내용"])}</td>'
            "</tr>"
        )
        for k in range(1, span):
            sub_row, _ = groups[i + k]
            body.append(
                "<tr>"
                f'<td style="{_CELL_STYLE}">{_esc(sub_row["부서"])}</td>'
                f'<td style="{_CELL_STYLE}">{_esc(sub_row["조치내용"])}</td>'
                "</tr>"
            )
        i += span
    header = "".join(f'<th style="{_HEAD_STYLE}">{h}</th>' for h in ("시간", "상황", "부서", "조치내용"))
    return (
        '<table style="width:100%; border-collapse:collapse; margin-top:6px;">'
        f"<tr>{header}</tr>{''.join(body)}</table>"
    )


def render_operation_log(operation_log: list[dict]) -> None:
    st.subheader("작전상황일지")
    if not operation_log:
        st.info("아직 기록된 사건이 없습니다.")
        return
    if not any(event.get("entries") for event in operation_log):
        st.info("기록된 세부 내용이 없습니다.")
        return

    st.caption(f"진행 중인 사태 {len(operation_log)}건 — 같은 상황의 후속 보고는 하나의 사태로 묶입니다.")
    st.markdown(_log_table_html(operation_log), unsafe_allow_html=True)

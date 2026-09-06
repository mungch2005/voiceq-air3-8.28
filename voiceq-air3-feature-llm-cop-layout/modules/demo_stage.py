"""시연 페이지의 '무대' 렌더링 — 전투지휘소 평면도와 상황실 4개를 그린다.

누가 말하고 있는지는 좌석·카드 강조로, 무슨 말을 했는지는 화면 하단 자막 바
(subtitle_html)로 보여준다. 말풍선은 자막 바와 같은 내용을 두 번 말하는 셈이라 두지
않는다.

전투지휘소는 HTML 상자가 아니라 SVG 평면도로 그린다. 좌석을 회의 테이블 둘레에
정확히 놓아야 "지휘소를 위에서 본 그림"으로 읽히는데, HTML flex로는 테이블과 좌석이
따로 떠 있는 배치밖에 안 나왔다. SVG는 컨테이너 높이가 vh로 변해도 비율을 지킨다.

색은 발표 자료에서 뽑은 modules/demo_theme의 토큰만 쓴다.

여기 있는 함수는 전부 문자열만 만들고 Streamlit을 부르지 않는다. 화면 없이 출력을
검사할 수 있어야 배치가 깨졌는지 확인하기 쉽기 때문이다. 실제 표출은 demo.py가 한다.
"""

from __future__ import annotations

import html

from modules import demo_rooms as dr
from modules import demo_theme as th
from modules import organization as org


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


C = th.COLORS

# 계급별 좌석 색. 영향력이 아니라 계급으로 칠한다 — 회의실을 봤을 때 누가 상급자인지가
# 먼저 읽혀야 하고, 영향력 수치는 화면에 드러내지 않는 내부 값이다.
_RANK_COLOR = {
    "준장": C["accent_bright"],
    "대령": "#2F6EA8",
    "중령": "#5A7CA8",
    "소령": "#4E6A90",
    "대위": "#46607F",
}
_DEFAULT_RANK_COLOR = "#46607F"


def section_label(text: str, right: str = "") -> str:
    """구역 제목. 전투지휘소·상황실 위에 같은 모양으로 붙여 두 칸의 시작선을 맞춘다."""
    tail = (
        f'<span style="font-size:0.58rem; font-weight:600; letter-spacing:0.5px; '
        f'color:{C["muted"]};">{_esc(right)}</span>'
        if right
        else ""
    )
    return (
        '<div style="display:flex; align-items:center; justify-content:space-between; '
        'gap:10px; margin:0 0 6px; height:15px;">'
        f'<span style="font-size:0.64rem; font-weight:800; letter-spacing:1.2px; '
        f'color:{C["accent_bright"]};">{_esc(text)}</span>{tail}</div>'
    )


# ---------- 전투지휘소 평면도 ----------

_VB_W, _VB_H = 880, 300


def _seat_svg(cx: float, cy: float, title: str, speaking: bool, label_below: bool) -> str:
    """좌석 하나 — 원형 배지(계급) + 직책 이름. 발언 중이면 링과 글로우가 켜진다."""
    info = org.lookup(title) or {}
    rank = str(info.get("rank", ""))
    fill = _RANK_COLOR.get(rank, _DEFAULT_RANK_COLOR)
    label_y = cy + 34 if label_below else cy - 24
    name_color = C["accent_bright"] if speaking else "rgba(233,240,250,0.78)"

    glow = (
        f'<circle cx="{cx}" cy="{cy}" r="26" fill="none" '
        f'stroke="{C["accent_bright"]}" stroke-width="1.5" opacity="0.35"/>'
        if speaking
        else ""
    )
    ring_stroke = C["accent_bright"] if speaking else "rgba(213,221,232,0.28)"
    ring_width = 2.4 if speaking else 1.2

    return (
        f"{glow}"
        f'<circle cx="{cx}" cy="{cy}" r="17" fill="{fill}" '
        f'stroke="{ring_stroke}" stroke-width="{ring_width}"/>'
        f'<text x="{cx}" y="{cy + 3.5}" text-anchor="middle" font-size="9.5" '
        f'font-weight="700" fill="#FFFFFF" opacity="0.92">{_esc(rank)}</text>'
        f'<text x="{cx}" y="{label_y}" text-anchor="middle" font-size="10.5" '
        f'font-weight="{700 if speaking else 500}" fill="{name_color}">{_esc(title)}</text>'
    )


def _spread(count: int, x0: float, x1: float) -> list[float]:
    """[x0, x1] 구간에 count개를 균등 배치한 중심 x 좌표."""
    if count <= 0:
        return []
    if count == 1:
        return [(x0 + x1) / 2]
    step = (x1 - x0) / (count - 1)
    return [x0 + step * i for i in range(count)]


def cp_html(speaker: str | None = None, height: str = "auto") -> str:
    """전투지휘소를 위에서 본 평면도.

    좌측 벽의 비디오월, 가운데 회의 테이블, 테이블 위/아래 좌석 줄, 오른쪽 상석
    (단장·부단장) 순으로 배치한다. 스케치의 배치를 그대로 따랐다.
    """
    room = dr.cp_room()
    people = dr.occupants(room["id"])
    head = [t for t in dr.load().get("head_seats", []) if t in people]
    rest = [t for t in people if t not in head]
    half = (len(rest) + 1) // 2
    top_row, bottom_row = rest[:half], rest[half:]

    parts: list[str] = [
        f'<svg viewBox="0 0 {_VB_W} {_VB_H}" width="100%" height="100%" '
        f'preserveAspectRatio="xMidYMid meet" xmlns="http://www.w3.org/2000/svg" '
        f'style="display:block;">',
        "<defs>",
        '<linearGradient id="vcFloor" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#0F2340"/>'
        '<stop offset="100%" stop-color="#081527"/></linearGradient>',
        '<linearGradient id="vcTable" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0%" stop-color="#1B3A5C"/>'
        '<stop offset="100%" stop-color="#0C1E33"/></linearGradient>',
        '<linearGradient id="vcScreen" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0%" stop-color="{C["accent_bright"]}" stop-opacity="0.85"/>'
        f'<stop offset="100%" stop-color="{C["accent"]}" stop-opacity="0.25"/>'
        "</linearGradient>",
        # 바닥 격자 — 평면도처럼 보이게 하는 최소한의 질감
        '<pattern id="vcGrid" width="40" height="40" patternUnits="userSpaceOnUse">'
        '<path d="M40 0H0V40" fill="none" stroke="rgba(213,221,232,0.055)" '
        'stroke-width="1"/></pattern>',
        "</defs>",
        # 실내 바닥
        f'<rect x="6" y="6" width="{_VB_W - 12}" height="{_VB_H - 12}" rx="10" '
        f'fill="url(#vcFloor)" stroke="rgba(213,221,232,0.16)"/>',
        f'<rect x="6" y="6" width="{_VB_W - 12}" height="{_VB_H - 12}" rx="10" '
        f'fill="url(#vcGrid)"/>',
    ]

    # 좌측 벽면 비디오월 — 스케치의 세로 바. 화면이므로 발광하는 면으로 그린다.
    parts += [
        '<rect x="25" y="44" width="18" height="212" rx="3" fill="#050C16" '
        f'stroke="{C["accent_bright"]}" stroke-opacity="0.55" stroke-width="1"/>',
        # 켜져 있는 화면이라는 표시 — 안쪽 발광 띠
        '<rect x="28" y="47" width="12" height="206" rx="2" fill="url(#vcScreen)" '
        'opacity="0.30"/>',
        f'<text x="58" y="150" text-anchor="middle" font-size="10" font-weight="700" '
        f'letter-spacing="3" fill="rgba(233,240,250,0.55)" '
        f'transform="rotate(-90 58 150)">VIDEO WALL</text>',
    ]

    # 회의 테이블
    parts += [
        '<rect x="170" y="126" width="478" height="54" rx="10" fill="url(#vcTable)" '
        'stroke="rgba(213,221,232,0.22)"/>',
        # 상판 윗면 림라이트 — 두께가 있는 가구로 읽히게 하는 최소한의 표현
        '<path d="M180 128 H638" stroke="rgba(233,240,250,0.16)" stroke-width="1.5" '
        'stroke-linecap="round"/>',
        '<line x1="186" y1="153" x2="632" y2="153" stroke="rgba(233,240,250,0.07)" '
        'stroke-width="1"/>',
    ]

    # 테이블 둘레 좌석
    for x, title in zip(_spread(len(top_row), 200, 618), top_row):
        parts.append(_seat_svg(x, 88, title, title == speaker, label_below=False))
    for x, title in zip(_spread(len(bottom_row), 200, 618), bottom_row):
        parts.append(_seat_svg(x, 218, title, title == speaker, label_below=True))

    # 상석 — 테이블 오른쪽 끝
    for y, title in zip(_spread(len(head), 122, 188) if len(head) > 1 else [153], head):
        parts.append(_seat_svg(706, y, title, title == speaker, label_below=True))

    parts.append("</svg>")

    return (
        f'<div class="vc-card" style="padding:9px 10px; height:{height}; '
        f'display:flex; flex-direction:column; box-sizing:border-box; overflow:hidden;">'
        + "".join(parts)
        + "</div>"
    )


# ---------- 상황실 ----------


def room_card_html(room: dict, speaker: str | None = None) -> str:
    """상황실 카드 하나. 상태 램프 + 인원 + 근무자 배지."""
    people = dr.occupants(room["id"])
    active = bool(speaker and speaker in people)
    cls = "vc-card is-speaking" if active else "vc-card"

    if active:
        lamp, state = C["accent_bright"], "발언 중"
    elif people:
        lamp, state = C["ok"], "대기"
    else:
        # 편제상 이 방에 배정된 화자가 없다. 빈 칸으로 두지 않고 그대로 밝힌다.
        lamp, state = C["muted"], "무인"

    if people:
        badges = "".join(
            f'<span style="display:inline-block; font-size:0.55rem; padding:2px 7px; '
            f'margin:3px 4px 0 0; border-radius:3px; '
            f'background:{"rgba(79,209,255,0.30)" if t == speaker and active else "rgba(255,255,255,0.07)"}; '
            f'border:1px solid {C["accent_bright"] if t == speaker and active else "rgba(213,221,232,0.14)"}; '
            f'color:{"#fff" if t == speaker and active else "rgba(233,240,250,0.72)"};">'
            f"{_esc(t)}</span>"
            for t in people
        )
    else:
        badges = "".join(
            f'<span style="display:inline-block; font-size:0.55rem; padding:2px 7px; '
            f'margin:3px 4px 0 0; border-radius:3px; '
            f'border:1px dashed rgba(213,221,232,0.20); color:rgba(233,240,250,0.34);">'
            f"{_esc(name)}</span>"
            for name in dr.unit_badges(room["id"])
        )

    count = f"{len(people)}명" if people else "—"
    return (
        f'<div class="{cls}" style="padding:9px 11px; min-height:92px; overflow:hidden; '
        f'display:flex; flex-direction:column;">'
        # 머리줄: 램프 + 방 이름 + 인원
        '<div style="display:flex; align-items:center; gap:6px; margin-bottom:2px;">'
        f'<span style="width:7px; height:7px; border-radius:50%; background:{lamp}; '
        f'box-shadow:0 0 7px {lamp}; flex:none;"></span>'
        f'<span style="font-size:0.68rem; font-weight:700; '
        f'color:{C["accent_bright"] if active else "rgba(233,240,250,0.92)"};">'
        f'{_esc(room["name"])}</span>'
        f'<span style="margin-left:auto; font-size:0.53rem; letter-spacing:0.5px; '
        f'color:{lamp if active else C["muted"]};">{_esc(state)} · {_esc(count)}</span>'
        "</div>"
        f'<div style="height:1px; background:rgba(213,221,232,0.12); margin:4px 0 0;"></div>'
        f'<div style="flex:1; min-height:0; display:flex; align-items:center; '
        f'line-height:1.45;"><div>{badges}</div></div>'
        "</div>"
    )


def rooms_grid_html(speaker: str | None = None, height: str = "auto") -> str:
    """상황실 4개를 2×2로. height를 주면 네 칸이 그 높이를 고르게 나눠 갖는다."""
    cards = "".join(room_card_html(r, speaker) for r in dr.situation_rooms())
    return (
        '<div style="display:grid; grid-template-columns:repeat(2, 1fr); '
        f'grid-template-rows:repeat(2, 1fr); gap:8px; height:{height}; '
        'box-sizing:border-box;">' + cards + "</div>"
    )


# ---------- 머리글 / 자막 ----------

# Streamlit 열 블록이 고정 높이(vh) 자식의 높이를 16px가량 짧게 잡아, 기본 간격만
# 믿으면 자막 바가 위 칸을 파고든다. 실측해서 얻은 여백이다.
_SUBTITLE_GAP = "22px"


def header_html(
    situation: str = "",
    latency: float | None = None,
    panels: int = 0,
    clock: str = "",
    processing: bool = False,
) -> str:
    """상단 상태 바 — 체계명, 현재 상황 유형, 표출 지연, 표출 화면 수, 시각.

    processing은 "발언은 끝났고 판단이 아직 안 나온" 박자에 켠다. 연속 재생에서
    말하기와 화면 전환 사이에 이 표시가 들어가야, 화면이 저절로 바뀌는 게 아니라
    발언을 처리한 결과라는 인과가 보인다.
    """

    def chip(label: str, value: str, color: str) -> str:
        return (
            '<div style="display:flex; flex-direction:column; align-items:flex-end; '
            'gap:1px; line-height:1;">'
            f'<span style="font-size:0.5rem; letter-spacing:1.5px; color:{C["muted"]};">'
            f"{_esc(label)}</span>"
            f'<span style="font-size:0.86rem; font-weight:800; color:{color}; '
            f'font-variant-numeric:tabular-nums;">{_esc(value)}</span></div>'
        )

    state = (
        f'<span style="font-size:0.66rem; font-weight:700; padding:3px 10px; '
        f'border-radius:3px; background:rgba(200,16,46,0.18); '
        f'border:1px solid {C["crit"]}; color:#FF8A9B; letter-spacing:1px;">'
        f"{_esc(situation)}</span>"
        if situation
        else f'<span style="font-size:0.62rem; color:{C["muted"]}; letter-spacing:1px;">'
        "상황 없음</span>"
    )

    chips = [chip("SCREENS", str(panels), "rgba(233,240,250,0.92)")]
    if processing:
        chips.append(chip("STATUS", "분석 중", C["warn"]))
    elif latency is not None:
        chips.append(chip("DISPLAY LATENCY", f"{latency:.1f}s", C["accent_bright"]))
    if clock:
        chips.append(chip("TIME", clock, "rgba(233,240,250,0.92)"))

    return (
        '<div style="display:flex; align-items:center; gap:16px; padding:0 2px 8px; '
        'border-bottom:1px solid rgba(213,221,232,0.16); margin-bottom:9px;">'
        f'<span style="font-size:1.05rem; font-weight:900; letter-spacing:3px; '
        f'color:{C["accent_bright"]};">VOICE-CUE</span>'
        f'<span style="font-size:0.62rem; letter-spacing:1.5px; color:{C["muted"]};">'
        "전투지휘소 상황판</span>"
        f'<span style="width:1px; height:16px; background:rgba(213,221,232,0.2);"></span>'
        + state
        + '<div style="margin-left:auto; display:flex; gap:22px; align-items:center;">'
        + "".join(chips)
        + "</div></div>"
    )


def subtitle_html(speaker: str | None, text: str | None, via_voice: bool = False) -> str:
    """하단 자막 바. 지금 발언 전문을 크게 보여준다 — 발언 내용이 나오는 유일한 자리다.

    화자만 있고 발언이 없으면 "말하는 중"이다. 연속 재생에서 녹음이 흐르는 동안이
    그 상태이고, 말이 끝나야 전사된 문장이 이 자리에 올라온다 — 실제 체계가 발언
    종료 후에 전사하는 순서와 같다. 그 사이 바를 비워 두면 사람은 말하는데 화면
    아래는 "발언 대기 중"이라고 적혀 있게 된다.
    """
    if not speaker:
        return (
            f'<div class="vc-card" style="margin-top:{_SUBTITLE_GAP}; padding:11px 14px; '
            f'font-size:0.72rem; letter-spacing:1px; color:{C["muted"]};">발언 대기 중…</div>'
        )

    info = org.lookup(speaker) or {}
    rank = str(info.get("rank", ""))
    room_id = dr.room_of(speaker)
    room_name = next((r["name"] for r in dr.rooms() if r["id"] == room_id), "")
    badge = (
        f'<span style="font-size:0.52rem; padding:2px 6px; border-radius:3px; '
        f'background:{C["ok"]}; color:#fff; letter-spacing:0.5px;">음성인식</span>'
        if via_voice
        else ""
    )

    return (
        f'<div class="vc-card" style="margin-top:{_SUBTITLE_GAP}; padding:0; '
        f'display:flex; align-items:stretch; '
        f'overflow:hidden; border-color:{C["accent_bright"]};">'
        # 좌측 화자 블록
        f'<div style="flex:none; padding:9px 13px; background:rgba(79,209,255,0.10); '
        f'border-right:1px solid rgba(79,209,255,0.35); display:flex; '
        f'flex-direction:column; gap:2px; justify-content:center; min-width:150px;">'
        '<div style="display:flex; align-items:center; gap:6px;">'
        f'<span style="font-size:0.78rem; font-weight:800; color:{C["accent_bright"]}; '
        f'white-space:nowrap;">{_esc(speaker)}</span>{badge}</div>'
        f'<span style="font-size:0.53rem; letter-spacing:0.5px; color:{C["muted"]}; '
        f'white-space:nowrap;">{_esc(rank)} · {_esc(room_name)}</span>'
        "</div>"
        # 발언 전문 — 아직 말하는 중이면 그 자리에 수신 표시가 대신 들어간다
        '<div style="flex:1; display:flex; align-items:center; padding:9px 15px; '
        'min-width:0;">'
        + (
            f'<span style="font-size:1rem; line-height:1.4; color:#EEF4FB;">{_esc(text)}</span>'
            if text
            else (
                f'<span style="font-size:0.78rem; letter-spacing:2px; color:{C["muted"]};">'
                "● 음성 수신 중…</span>"
            )
        )
        + "</div></div>"
    )

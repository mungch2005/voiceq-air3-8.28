"""COP 타일 안에 띄울 합성 화면 콘텐츠 — 실제 카메라·레이더 피드가 없는 프로토타입에서
"이름표만 붙은 회색 칸" 대신 그럴듯한 화면처럼 보이게 한다.

원칙은 지도(map_renderer.py)와 같다 — 없는 걸 지어내 오도하지 않는다. 그래서:
- 화면 "종류"(카메라 장면·레이더 스코프·기상 패널 등)는 소스의 실제 feed_type/category로
  결정한다. 소스가 CCTV면 카메라 장면을, 레이더 계열이면 스코프를 보여주는 식이다.
- 화면 안의 텍스트(이름표·태그 기반 상태 항목)는 screen_sources.json에 이미 있는 실제
  카탈로그 데이터에서만 가져온다. 좌표·수치(항적 위치, 기온 등)처럼 실제로는 센서가
  있어야 아는 값만 소스 id 기반 결정론적 시드로 합성한다 — 새로고침마다 바뀌지 않고,
  "그럴듯한 하나의 값"으로 고정된다.
- LLM은 이 렌더링에 전혀 관여하지 않는다. 상황 유형 분류 → 플레이북 → 소스 id까지는
  기존 경로 그대로고, 이 모듈은 그 소스 id를 코드가 그림으로 바꾸는 마지막 단계다.
"""

from __future__ import annotations

import hashlib
import html
import time


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _seed(source_id: str, salt: str = "") -> int:
    """소스 id(+salt)에 항상 같은 정수를 매핑. 실제 센서값이 없는 자리를 채우되
    새로고침마다 흔들리지 않게 하기 위함(map_renderer._jitter와 같은 목적)."""
    return int(hashlib.md5(f"{source_id}:{salt}".encode()).hexdigest(), 16)


def _pick(source_id: str, salt: str, options: list):
    return options[_seed(source_id, salt) % len(options)]


def _range(source_id: str, salt: str, lo: float, hi: float, digits: int = 0) -> float:
    n = _seed(source_id, salt) % 10_000
    val = lo + (hi - lo) * (n / 10_000)
    return round(val, digits)


_STATUS_ROW = (
    '<div style="display:flex; align-items:center; gap:7px; padding:4px 2px;">'
    '<span style="width:8px; height:8px; border-radius:50%; background:{color}; '
    'flex-shrink:0; box-shadow:0 0 4px {color};"></span>'
    '<span style="flex:1; font-size:0.68rem; opacity:0.85;">{label}</span>'
    '<span style="font-family:monospace; font-size:0.62rem; opacity:0.6;">{value}</span>'
    "</div>"
)

_OK_COLOR = "#4CAF6D"
_WARN_COLOR = "#D9A441"


def _status_light(source_id: str, salt: str, ok_ratio: float = 0.85) -> str:
    """대부분 정상(초록), 가끔 주의(황색) — 화면이 전부 초록불이면 오히려 정지 화면처럼
    보인다. 소스 id로 고정된 소수만 주의 상태로 둔다."""
    return _OK_COLOR if (_seed(source_id, salt) % 100) / 100 < ok_ratio else _WARN_COLOR


def _fill_wrap(svg_open_tail: str, svg_body: str, bg: str = "transparent") -> str:
    """SVG 화면(카메라·레이더·위성구름 등)을 타일에 꽉 채우되, 그 타일이 속한 grid
    row의 높이 계산에는 관여하지 않게 감싼다.

    타일이 놓이는 grid row는 높이가 "auto"라 내용물 크기에 맞춰 정해지는데,
    svg에 height:100%만 주면 이 auto 높이가 아직 안 정해진 상태라 퍼센트가
    풀리지 않고, 브라우저가 viewBox 고유 비율(가로:세로)로 되돌아가 버린다.
    그러면 그 "고유 비율로 계산된 높이"가 오히려 grid row 자체를 그만큼
    부풀려서, 같은 줄의 다른(SVG 아닌) 화면보다 그 타일만 훨씬 커 보이는
    문제가 생긴다(예: TOD·SSR이 비행 스케줄·상황판보다 두 배 가까이 컸던 원인).
    position:absolute + inset:0으로 빼면 이 svg는 부모 크기 계산에서 완전히
    빠지고, 타일은 플레이북이 배정한 크기(grid-row/column span) 그대로 유지된다.
    """
    svg = f'<svg {svg_open_tail} style="position:absolute; inset:0; width:100%; height:100%; display:block;">{svg_body}</svg>'
    return (
        f'<div style="position:relative; flex:1; min-height:0; overflow:hidden; background:{bg};">'
        f"{svg}</div>"
    )


# ---------------------------------------------------------------------------
# 카메라 계열 (CCTV · 열상 · 바디캠 · 조준경 · 이동형 · 출입통제 ANPR)
# ---------------------------------------------------------------------------

_FENCE_CATEGORIES = {"외곽경계", "취약지점", "순찰로", "초소"}
_BUILDING_CATEGORIES = {"시설", "부대시설", "체육화면"}
_TARMAC_CATEGORIES = {"활주로", "유도로", "계류장"}


def _scene_silhouette(source_id: str, category: str) -> str:
    """장면 실루엣 — 카테고리별로 다른 지형지물을 암시한다. 정밀한 그림이 아니라
    "이 카메라가 대충 뭘 보고 있는지" 알 수 있는 수준의 스케치."""
    if category in _FENCE_CATEGORIES:
        posts = "".join(
            f'<line x1="{x}" y1="95" x2="{x}" y2="130" stroke="#22282A" stroke-width="3"/>'
            for x in range(10, 320, 34)
        )
        wires = "".join(
            f'<line x1="6" y1="{y}" x2="316" y2="{y}" stroke="#22282A" stroke-width="1" opacity="0.8"/>'
            for y in (98, 108, 118)
        )
        return posts + wires
    if category in _BUILDING_CATEGORIES:
        bldgs = []
        x = 14
        for i in range(5):
            w = 30 + _seed(source_id, f"bw{i}") % 26
            h = 20 + _seed(source_id, f"bh{i}") % 45
            bldgs.append(
                f'<rect x="{x}" y="{130 - h}" width="{w}" height="{h}" fill="#1B2226"/>'
            )
            x += w + 12
            if x > 300:
                break
        return "".join(bldgs)
    if category in _TARMAC_CATEGORIES:
        lines = "".join(
            f'<line x1="{130 + i * 6}" y1="130" x2="{155 + i * 22}" y2="60" '
            f'stroke="#2A3134" stroke-width="2"/>'
            for i in (-2, -1, 0, 1, 2)
        )
        dashes = "".join(
            f'<rect x="158" y="{130 - i * 16}" width="4" height="8" fill="#4A5055" opacity="0.7"/>'
            for i in range(5)
        )
        return lines + dashes
    return ""  # 개활지·감시 위주 — 지형지물 없이 지평선만


def _osd_chrome(source_id: str, name: str, tone: str) -> str:
    """모든 카메라류 화면에 공통으로 얹는 오버레이 — REC 표시, 타임스탬프, AF 코너
    브래킷. 실제 CCTV/캠코더 화면표시(OSD)를 흉내낸 것."""
    ink = "#8FE6B0" if tone == "thermal" else "#E8EAEC"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    corners = "".join(
        f'<path d="{d}" stroke="{ink}" stroke-width="1.4" fill="none" opacity="0.55"/>'
        for d in (
            "M10,10 L10,20 M10,10 L20,10",
            "M310,10 L310,20 M310,10 L300,10",
            "M10,170 L10,160 M10,170 L20,170",
            "M310,170 L310,160 M310,170 L300,170",
        )
    )
    return (
        corners
        + f'<circle cx="18" cy="30" r="3.5" fill="#E63946">'
        + f'<animate attributeName="opacity" values="1;0.25;1" dur="1.6s" repeatCount="indefinite"/>'
        + "</circle>"
        + f'<text x="27" y="33" fill="#E63946" font-size="9" font-family="monospace" '
        + f'font-weight="700" opacity="0.9">REC</text>'
        + f'<text x="309" y="164" fill="{ink}" font-size="8" font-family="monospace" '
        + f'text-anchor="end" opacity="0.75">{_esc(now)}</text>'
        + f'<text x="11" y="164" fill="{ink}" font-size="8" font-family="monospace" '
        + f'opacity="0.6">{_esc(source_id)}</text>'
    )


def _camera(source: dict) -> str:
    source_id = source["id"]
    category = source.get("category", "")
    feed_type = source.get("feed_type", "")
    tags = source.get("tags", [])
    name = source.get("name", source_id)

    is_thermal = feed_type == "열상"
    is_night = ("야간" in tags) and not is_thermal
    is_scope = feed_type == "조준경"
    is_bodycam = feed_type == "바디캠"
    is_mobile = feed_type == "이동형"
    is_anpr = category == "출입통제"

    if is_thermal:
        sky, ground = "#0B1220", "#0A1A16"
        blob_x = 60 + _seed(source_id, "bx") % 200
        blob_y = 90 + _seed(source_id, "by") % 30
        heat = (
            f'<defs><radialGradient id="heat{hash(source_id) & 0xffff}" cx="50%" cy="50%" r="50%">'
            '<stop offset="0%" stop-color="#FFF3C4"/><stop offset="45%" stop-color="#F4A261"/>'
            '<stop offset="100%" stop-color="#8A3033" stop-opacity="0"/></radialGradient></defs>'
            f'<ellipse cx="{blob_x}" cy="{blob_y}" rx="16" ry="26" '
            f'fill="url(#heat{hash(source_id) & 0xffff})"/>'
        )
        scene = heat
        tone = "thermal"
    else:
        sky = "#141B15" if is_night else "#1C2B24"
        ground = "#0D1410" if is_night else "#152018"
        scene = _scene_silhouette(source_id, category)
        tone = "night" if is_night else "day"

    reticle = ""
    if is_scope:
        cx, cy = 160, 90
        reticle = (
            f'<line x1="0" y1="{cy}" x2="320" y2="{cy}" stroke="#9FE6B0" stroke-width="0.6" opacity="0.7"/>'
            f'<line x1="{cx}" y1="0" x2="{cx}" y2="180" stroke="#9FE6B0" stroke-width="0.6" opacity="0.7"/>'
            + "".join(
                f'<line x1="{cx - 60 + i * 20}" y1="{cy - 3}" x2="{cx - 60 + i * 20}" y2="{cy + 3}" '
                f'stroke="#9FE6B0" stroke-width="0.6" opacity="0.6"/>'
                for i in range(7)
            )
            + f'<circle cx="{cx}" cy="{cy}" r="34" fill="none" stroke="#9FE6B0" stroke-width="0.8" opacity="0.5"/>'
            # 스코프 튜브 비네트 — 두꺼운 링 하나로 화면 가장자리만 어둡게 눌러
            # 원형 조준경을 들여다보는 느낌을 낸다.
            + f'<circle cx="{cx}" cy="{cy}" r="150" fill="none" stroke="#000" stroke-width="60" opacity="0.35"/>'
        )
        target_x = 160 + (_seed(source_id, "tx") % 60 - 30)
        target_y = 80 + (_seed(source_id, "ty") % 20 - 10)
        reticle += (
            f'<path d="M{target_x-6},{target_y} l4,-4 l4,4 l-4,4 z" fill="#E63946"/>'
            f'<text x="{target_x + 10}" y="{target_y + 3}" fill="#E63946" font-size="7" '
            f'font-family="monospace">TRK</text>'
        )

    badge = ""
    if is_mobile:
        speed = int(_range(source_id, "speed", 8, 38))
        badge = (
            '<rect x="252" y="12" width="58" height="16" rx="3" fill="rgba(0,0,0,0.5)" stroke="#4CAF6D" stroke-width="0.8"/>'
            f'<text x="281" y="23" fill="#4CAF6D" font-size="8" font-family="monospace" '
            f'text-anchor="middle">{speed}km/h</text>'
        )
    if is_anpr:
        plate = (
            f"{_seed(source_id, 'p1') % 90 + 10}"
            f"{_pick(source_id, 'p2', list('가나다라마바사아자'))}"
            f" {_seed(source_id, 'p3') % 9000 + 1000}"
        )
        badge += (
            '<rect x="96" y="146" width="128" height="18" rx="3" fill="rgba(10,20,10,0.75)" '
            'stroke="#4CAF6D" stroke-width="0.8"/>'
            f'<text x="160" y="159" fill="#4CAF6D" font-size="9" font-family="monospace" '
            f'text-anchor="middle" font-weight="700">인식됨 {_esc(plate)}</text>'
        )

    battery = ""
    transform_open, transform_close = "", ""
    if is_bodycam:
        angle = (_seed(source_id, "rot") % 7) - 3
        transform_open = f'<g transform="rotate({angle} 160 90)">'
        transform_close = "</g>"
        pct = int(_range(source_id, "batt", 40, 97))
        battery = (
            '<rect x="252" y="12" width="30" height="14" rx="2" fill="none" stroke="#E8EAEC" opacity="0.7"/>'
            '<rect x="282" y="16" width="3" height="6" fill="#E8EAEC" opacity="0.7"/>'
            f'<rect x="254" y="14" width="{max(2, int(26 * pct / 100))}" height="10" fill="#4CAF6D"/>'
            f'<text x="267" y="35" fill="#E8EAEC" font-size="7" text-anchor="middle" '
            f'font-family="monospace" opacity="0.7">{pct}%</text>'
        )

    scanlines = "".join(
        f'<line x1="0" y1="{y}" x2="320" y2="{y}" stroke="#000" stroke-width="1" opacity="0.06"/>'
        for y in range(0, 180, 5)
    )

    # "slice"는 타일을 꽉 채우려고 viewBox 밖으로 넘치는 부분을 잘라내는데, 실제
    # 타일 비율이 이 16:9 캔버스보다 정사각형에 가까우면 그 잘려나가는 부분에
    # 타임스탬프·id 같은 화면 가장자리 글자가 걸려 잘려 보였다. "meet"은 절대
    # 내용을 자르지 않고 남는 자리를 여백(레터박스)으로 두므로, 그 여백을 타일과
    # 같은 어두운 배경으로 칠해 모니터 베젤처럼 보이게 한다.
    body = (
        f'<rect width="320" height="95" fill="{sky}"/>'
        f'<rect y="95" width="320" height="85" fill="{ground}"/>'
        f"{transform_open}{scene}{transform_close}"
        f"{scanlines}{reticle}{badge}{battery}"
        f"{_osd_chrome(source_id, name, tone)}"
    )
    return _fill_wrap(
        'viewBox="0 0 320 180" preserveAspectRatio="xMidYMid meet" '
        'xmlns="http://www.w3.org/2000/svg"',
        body,
        bg="#05080B",
    )


# ---------------------------------------------------------------------------
# 레이더/탐지 계열
# ---------------------------------------------------------------------------


def _radar(source: dict) -> str:
    source_id = source["id"]
    is_uas = source_id.startswith("UAS") or source_id == "SYS-AD-WATCH"
    n_blips = 1 + _seed(source_id, "nb") % (3 if is_uas else 5)
    sweep_angle = _seed(source_id, "sweep") % 360

    rings = "".join(
        f'<circle cx="100" cy="100" r="{r}" fill="none" stroke="#2E4A3A" stroke-width="0.7" opacity="0.7"/>'
        for r in (30, 55, 80)
    )
    ticks = "".join(
        f'<line x1="{100 + 78*_cos(a)}" y1="{100 + 78*_sin(a)}" '
        f'x2="{100 + 84*_cos(a)}" y2="{100 + 84*_sin(a)}" stroke="#3A5C48" stroke-width="1"/>'
        for a in range(0, 360, 30)
    )
    cardinal = "".join(
        f'<text x="{100 + 92*_cos(a)}" y="{103 + 92*_sin(a)}" fill="#6FA383" font-size="8" '
        f'text-anchor="middle" font-family="monospace">{lbl}</text>'
        for a, lbl in ((270, "N"), (0, "E"), (90, "S"), (180, "W"))
    )
    sweep = (
        f'<path d="M100,100 L{100 + 82*_cos(sweep_angle)},{100 + 82*_sin(sweep_angle)} '
        f'A82,82 0 0,1 {100 + 82*_cos(sweep_angle+40)},{100 + 82*_sin(sweep_angle+40)} Z" '
        f'fill="#4CAF6D" opacity="0.14"/>'
    )

    blips = []
    for i in range(n_blips):
        a = _seed(source_id, f"a{i}") % 360
        r = 20 + _seed(source_id, f"r{i}") % 60
        bx, by = 100 + r * _cos(a), 100 + r * _sin(a)
        if is_uas:
            blips.append(
                f'<path d="M{bx-4},{by} l4,-6 l4,6 l-4,4 z" fill="#E63946"/>'
                f'<text x="{bx+8}" y="{by-2}" fill="#E63946" font-size="6.5" '
                f'font-family="monospace">UAV{i+1}</text>'
            )
        else:
            trail = "".join(
                f'<circle cx="{bx - k*3}" cy="{by - k*2}" r="{2.2 - k*0.4}" '
                f'fill="#4CAF6D" opacity="{0.5 - k*0.15}"/>'
                for k in range(1, 3)
            )
            blips.append(
                trail + f'<circle cx="{bx}" cy="{by}" r="2.6" fill="#8FE6B0"/>'
                + (
                    f'<text x="{bx+6}" y="{by-3}" fill="#8FE6B0" font-size="6" '
                    f'font-family="monospace" opacity="0.85">FL{100+_seed(source_id, f"fl{i}")%250}</text>'
                    if source_id == "RDR-SSR" else ""
                )
            )

    body = (
        '<rect width="200" height="200" fill="#081008"/>'
        f"{rings}{ticks}{cardinal}{sweep}"
        '<circle cx="100" cy="100" r="2" fill="#8FE6B0"/>'
        + "".join(blips)
        + f'<text x="6" y="194" fill="#6FA383" font-size="7" font-family="monospace" opacity="0.7">'
        f'{_esc(source["id"])}</text>'
    )
    return _fill_wrap(
        'viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg"', body, bg="#081008"
    )


def _cos(deg: float) -> float:
    import math

    return math.cos(math.radians(deg))


def _sin(deg: float) -> float:
    import math

    return math.sin(math.radians(deg))


# ---------------------------------------------------------------------------
# 기상
# ---------------------------------------------------------------------------


def _weather(source: dict) -> str:
    source_id = source["id"]
    if source_id == "MET-SAT":
        blobs = "".join(
            f'<ellipse cx="{20+_seed(source_id, f"cx{i}")%280}" cy="{20+_seed(source_id, f"cy{i}")%140}" '
            f'rx="{18+_seed(source_id, f"rx{i}")%26}" ry="{10+_seed(source_id, f"ry{i}")%16}" '
            f'fill="#E8EAEC" opacity="{0.12 + (_seed(source_id, f"o{i}")%30)/100}"/>'
            for i in range(9)
        )
        body = (
            '<rect width="320" height="180" fill="#0B1830"/>' + blobs +
            '<text x="10" y="170" fill="#9FB8D9" font-size="8" font-family="monospace" opacity="0.7">'
            'IR ENHANCED · KMA-GK2A</text>'
        )
        return _fill_wrap(
            'viewBox="0 0 320 180" xmlns="http://www.w3.org/2000/svg"', body, bg="#0B1830"
        )

    if source_id == "MET-RDR":
        patches = "".join(
            f'<circle cx="{100+_seed(source_id, f"px{i}")%80-40}" cy="{100+_seed(source_id, f"py{i}")%80-40}" '
            f'r="{10+_seed(source_id, f"pr{i}")%18}" fill="{_pick(source_id, f"pc{i}", ["#4CAF6D","#D9A441","#8A3033"])}" '
            f'opacity="0.3"/>'
            for i in range(5)
        )
        rings = "".join(
            f'<circle cx="100" cy="100" r="{r}" fill="none" stroke="#2E4A3A" stroke-width="0.6" opacity="0.6"/>'
            for r in (30, 55, 80)
        )
        body = (
            '<rect width="200" height="200" fill="#081008"/>'
            + rings + patches +
            '<circle cx="100" cy="100" r="2" fill="#8FE6B0"/>'
        )
        return _fill_wrap(
            'viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg"', body, bg="#081008"
        )

    if source_id == "MET-RVR":
        meters = int(_range(source_id, "m", 350, 2000))
        color = _OK_COLOR if meters > 800 else _WARN_COLOR if meters > 400 else "#8A3033"
        pct = min(100, meters / 20)
        return (
            '<div style="flex:1; display:flex; flex-direction:column; align-items:center; '
            'justify-content:center; gap:8px; padding:10px;">'
            f'<div style="font-family:monospace; font-size:1.6rem; font-weight:700; color:{color};">'
            f"{meters}<span style=\"font-size:0.9rem; opacity:0.7;\"> m</span></div>"
            f'<div style="width:80%; height:6px; background:rgba(255,255,255,0.1); border-radius:3px; overflow:hidden;">'
            f'<div style="width:{pct}%; height:100%; background:{color};"></div></div>'
            '<div style="font-size:0.62rem; opacity:0.6;">활주로 가시거리 · RVR</div></div>'
        )

    if source_id == "MET-FCST":
        rows = []
        for i in range(4):
            hour = (int(time.strftime("%H")) + i * 3) % 24
            temp = int(_range(source_id, f"t{i}", 8, 24))
            pop = int(_range(source_id, f"p{i}", 0, 90))
            icon = _pick(source_id, f"ic{i}", ["☀", "⛅", "☁", "🌧"])
            rows.append(
                f'<div style="display:flex; justify-content:space-between; padding:3px 4px; '
                f'font-size:0.68rem; border-bottom:1px solid rgba(255,255,255,0.06);">'
                f'<span style="opacity:0.7;">{hour:02d}시</span><span>{icon}</span>'
                f'<span style="font-family:monospace;">{temp}°C</span>'
                f'<span style="font-family:monospace; opacity:0.6;">{pop}%</span></div>'
            )
        return '<div style="flex:1; padding:6px 10px;">' + "".join(rows) + "</div>"

    # MET-OBS — 종합 관측
    metrics = [
        ("기온", f'{_range(source_id, "temp", -5, 30, 1)}°C'),
        ("풍향/풍속", f'{_pick(source_id, "wd", ["N","NE","E","SE","S","SW","W","NW"])} {_range(source_id, "ws", 1, 12, 1)}m/s'),
        ("시정", f'{_range(source_id, "vis", 0.5, 10, 1)}km'),
        ("운고", f'{int(_range(source_id, "clg", 300, 3000))}ft'),
    ]
    cells = "".join(
        f'<div style="text-align:center; padding:6px;">'
        f'<div style="font-size:0.6rem; opacity:0.55;">{k}</div>'
        f'<div style="font-family:monospace; font-size:0.82rem; font-weight:700; margin-top:2px;">{v}</div></div>'
        for k, v in metrics
    )
    return (
        '<div style="flex:1; display:grid; grid-template-columns:1fr 1fr; '
        'align-content:center;">' + cells + "</div>"
    )


# ---------------------------------------------------------------------------
# 항행안전 장비 상태
# ---------------------------------------------------------------------------


def _nav_status(source: dict) -> str:
    source_id = source["id"]
    ok = (_seed(source_id, "ok") % 100) / 100 < 0.9
    color = _OK_COLOR if ok else _WARN_COLOR
    label = "정상" if ok else "점검 중"
    uptime = _range(source_id, "up", 97.0, 99.99, 2)
    checked = f"{(int(time.strftime('%H')) - 1) % 24:02d}:{_seed(source_id, 'mm') % 60:02d}"
    return (
        '<div style="flex:1; display:flex; flex-direction:column; align-items:center; '
        'justify-content:center; gap:6px; padding:8px;">'
        f'<div style="width:14px; height:14px; border-radius:50%; background:{color}; '
        f'box-shadow:0 0 8px {color};"></div>'
        f'<div style="font-size:0.85rem; font-weight:700; color:{color};">{label}</div>'
        f'<div style="font-family:monospace; font-size:0.65rem; opacity:0.6;">가동률 {uptime}%</div>'
        f'<div style="font-size:0.6rem; opacity:0.45;">최근 점검 {checked}</div></div>'
    )


# ---------------------------------------------------------------------------
# 비행 스케줄
# ---------------------------------------------------------------------------


def _schedule(source: dict) -> str:
    source_id = source["id"]
    statuses = ["착륙완료", "접근중", "대기", "이륙완료"]
    rows = []
    base_hour = int(time.strftime("%H"))
    for i in range(4):
        callsign = f"{_pick(source_id, f'u{i}', ['ROKAF','KAF','ORE'])}-{1000 + _seed(source_id, f'c{i}') % 8999}"
        hh = (base_hour - 1 + i) % 24
        mm = _seed(source_id, f"m{i}") % 60
        status = statuses[min(i, len(statuses) - 1)]
        rows.append(
            '<div style="display:flex; justify-content:space-between; gap:6px; padding:4px 6px; '
            'font-size:0.66rem; border-bottom:1px solid rgba(255,255,255,0.06);">'
            f'<span style="font-family:monospace;">{hh:02d}:{mm:02d}</span>'
            f'<span style="flex:1; text-align:left; padding-left:8px; opacity:0.85;">{_esc(callsign)}</span>'
            f'<span style="opacity:0.6;">{status}</span></div>'
        )
    return '<div style="flex:1; padding:6px 8px; overflow:auto;">' + "".join(rows) + "</div>"


# ---------------------------------------------------------------------------
# 일반 상태 대시보드 (그 외 체계화면)
# ---------------------------------------------------------------------------

_SKIP_TAGS = {"현황", "화면"}


def _dashboard(source: dict) -> str:
    source_id = source["id"]
    tags = [t for t in source.get("tags", []) if t not in _SKIP_TAGS] or ["상태"]
    rows = []
    for i, tag in enumerate(tags[:5]):
        color = _status_light(source_id, f"row{i}")
        value = "정상" if color == _OK_COLOR else "주의"
        rows.append(_STATUS_ROW.format(color=color, label=_esc(tag), value=value))
    return (
        '<div style="flex:1; display:flex; flex-direction:column; justify-content:center; '
        'gap:2px; padding:8px 12px;">' + "".join(rows) + "</div>"
    )


# ---------------------------------------------------------------------------
# 진입점
# ---------------------------------------------------------------------------

_CAMERA_FEED_TYPES = {"CCTV", "열상", "바디캠", "조준경", "이동형"}


def render(source: dict) -> str:
    """소스 카탈로그 레코드 -> 그 화면 안에 들어갈 body HTML.

    source는 sources.by_id()에서 얻은 원본 레코드(id/name/category/feed_type/tags)여야
    한다 — cop_layout 항목 자체에는 이 필드들이 없다."""
    source_id = source.get("id", "")
    category = source.get("category", "")
    feed_type = source.get("feed_type", "")

    if source_id == "SYS-AD-WATCH" or category == "탐지체계":
        return _radar(source)
    if category == "기상":
        return _weather(source)
    if category == "항행안전":
        return _nav_status(source)
    if source_id == "ACFT-SCHED":
        return _schedule(source)
    if feed_type in _CAMERA_FEED_TYPES or category == "출입통제":
        return _camera(source)
    return _dashboard(source)

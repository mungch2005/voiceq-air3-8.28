"""가상비행단 배치도 SVG 렌더링 — 위성사진 느낌의 기지 조감도.

이 지도는 상황에 따라 바뀌지 않는 "고정 배치도"다. 지도 위에는 두 가지만
얹는다 — ① 카탈로그에 있는 모든 CCTV의 위치(회색 점), ② 지금 COP 화면
(비디오월)에 떠 있는 CCTV의 위치(빨간 점). 항적·경보수준·자산 가동상태처럼
LLM의 추정이 필요한 정보는 올리지 않는다 — 정확한 위치를 모르는 것을 지도에
억지로 찍으면 "그 지점에 실재한다"는 그림이 되어 보는 사람을 오도하기 때문이다.
"""

from __future__ import annotations

import hashlib
import html
from functools import lru_cache

from modules import base_map as bm
from modules import sources

CELL = 78
MARGIN_LEFT = 30
MARGIN_TOP = 26
SUBDIV = 4  # 주격자 한 칸을 몇 등분해 보조격자를 그릴지

# CCTV로 표시할 feed_type. "시스템"(상황판·레이더 화면 등)은 실제로
# 특정 지점을 비추는 카메라가 아니므로 지도에 찍지 않는다.
CAMERA_FEED_TYPES = {"CCTV", "열상", "바디캠", "이동형", "조준경"}

EXISTING_CAMERA_COLOR = "#6E8A99"
ACTIVE_CAMERA_COLOR = "#E63946"

# 위성사진 느낌을 내기 위한 팔레트 (지표 · 포장 · 건물 지붕)
TERRAIN = "#2B3A2E"
TERRAIN_LIGHT = "#35472F"
ASPHALT = "#2E3236"
CONCRETE = "#4A4E52"
ROAD = "#3A3E42"

ROOF = {
    "hangar": "#5A6570", "tower": "#6B5E7A", "ops": "#4E5A66",
    "depot": "#6B5348", "living": "#55606B", "gate": "#4A5058",
    "radar": "#3F5A5E", "apron": CONCRETE, "taxiway": ASPHALT, "runway": ASPHALT,
}


def _esc(text: object) -> str:
    return html.escape(str(text), quote=True)


def _center(cell: str) -> tuple[float, float] | None:
    idx = bm.cell_to_index(cell)
    if idx is None:
        return None
    col, row = idx
    return MARGIN_LEFT + col * CELL + CELL / 2, MARGIN_TOP + row * CELL + CELL / 2


def _rect_for(cells: list[str]) -> tuple[float, float, float, float] | None:
    pts = [_center(c) for c in cells if bm.cell_to_index(c)]
    pts = [p for p in pts if p]
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs) - CELL / 2, min(ys) - CELL / 2,
            max(xs) - min(xs) + CELL, max(ys) - min(ys) + CELL)


def _jitter(seed: str, span: int) -> int:
    """같은 입력에는 항상 같은 값. 지형 얼룩을 흩뿌리되 새로고침해도 안 흔들리게."""
    return int(hashlib.md5(seed.encode()).hexdigest(), 16) % span


def _existing_camera_cells() -> list[tuple[float, float]]:
    """카탈로그에 있는 모든 카메라의 위치. 격자 1칸에 여러 대가 있어도 점 하나로 합친다."""
    cells: set[str] = set()
    for source in sources.load_catalog():
        if source["feed_type"] in CAMERA_FEED_TYPES:
            cells.add(source["cell"])
    points = [pos for cell in cells if (pos := _center(cell))]
    return points


def _active_camera_markers(active_sources: list[dict] | None) -> list[tuple[float, float, str]]:
    """cop_layout 항목 중 실제 카메라 피드만 골라 (x, y, 라벨)로 변환한다."""
    if not active_sources:
        return []
    catalog = sources.by_id()
    markers = []
    for item in active_sources:
        source_id = item.get("source_id", "")
        catalog_entry = catalog.get(source_id)
        feed_type = catalog_entry["feed_type"] if catalog_entry else ""
        if feed_type not in CAMERA_FEED_TYPES:
            continue
        pos = _center(item.get("cell", ""))
        if not pos:
            continue
        markers.append((pos[0], pos[1], str(item.get("priority", ""))))
    return markers


def _map_dimensions() -> tuple[int, int, int, int]:
    """(전체 너비, 전체 높이, 열 개수, 행 개수). 래스터 피커 이미지도 이 좌표계를 그대로 쓴다."""
    base = bm.load_base_map()
    grid = base["base"]["grid"]
    cols, rows = grid["cols"], grid["rows"]
    W = MARGIN_LEFT + len(cols) * CELL + 12
    H = MARGIN_TOP + rows * CELL + 12
    return W, H, len(cols), rows


@lru_cache(maxsize=1)
def _named_points() -> list[tuple[str, float, float]]:
    """시설·초소·대공자산의 (이름, x, y). 수동 배치 아이콘의 최근접 지명을 찾는 데 쓴다."""
    base = bm.load_base_map()
    points: list[tuple[str, float, float]] = []
    for fac in base["facilities"]:
        r = _rect_for(fac["cells"])
        if r:
            x, y, w, h = r
            points.append((fac["name"], x + w / 2, y + h / 2))
    for post in base["sentry_posts"]:
        c = _center(post["cell"])
        if c:
            points.append((post["name"], c[0], c[1]))
    for asset in base["air_defense"]:
        c = _center(asset["cell"])
        if c:
            points.append((asset["name"], c[0], c[1]))
    return points


def nearest_facility_name(x: float, y: float) -> str:
    """지도 위 임의의 픽셀 좌표에서 가장 가까운 시설/초소/대공자산 이름."""
    points = _named_points()
    if not points:
        return "미상 위치"
    name, _, _ = min(points, key=lambda p: (p[1] - x) ** 2 + (p[2] - y) ** 2)
    return name


def facility_center(name: str) -> tuple[float, float] | None:
    """시설/초소/대공자산 이름 -> 지도 픽셀 좌표. 발언에 언급된 지명을 아이콘 자동
    배치 위치로 바꿀 때 쓴다(context_memory._auto_place_markers)."""
    for pname, x, y in _named_points():
        if pname == name:
            return (x, y)
    return None


@lru_cache(maxsize=1)
def _direction_anchors() -> dict[str, tuple[str, float, float]]:
    """방위(북/남/동/서/북서 등) -> 그 구역을 담당하는 경계초소의 (이름, x, y).

    발언이 "기지 동쪽에서 무인기 식별"처럼 시설명 없이 방위만 말하는 경우가
    많다. 정확한 지점을 모르는데 억지로 찍을 수는 없으니, 그 방위를 담당하는
    경계초소(base_map.json sentry_posts[].sector) 위치를 근사치로 쓴다.
    """
    base = bm.load_base_map()
    anchors: dict[str, tuple[str, float, float]] = {}
    for post in base["sentry_posts"]:
        sector = post.get("sector", "")
        c = _center(post["cell"])
        if sector and c:
            anchors[sector] = (post["name"], c[0], c[1])
    return anchors


def direction_anchor(direction: str) -> tuple[str, float, float] | None:
    """방위 -> (경계초소 이름, x, y). 해당 방위를 담당하는 초소가 없으면 None."""
    return _direction_anchors().get(direction)


def render_picker_image(markers: list[dict] | None = None, selected_idx: int | None = None):
    """수동 배치용 클릭 대상 래스터 이미지. 한글/이모지 폰트 의존 없이 격자·시설 윤곽·
    배치된 마커만 그린다 — 실제 이름/이모지는 Streamlit 쪽 텍스트로 보여준다.

    selected_idx: 지금 조작 대상으로 고른 마커의 인덱스(0-based). 같은 종류 상황이
    동시에 여럿(북쪽 무인기·동쪽 무인기 등)일 때 어느 아이콘이 지금 클릭으로
    옮겨질지 강조 테두리로 보여준다."""
    from PIL import Image, ImageDraw

    W, H, ncols, nrows = _map_dimensions()
    x0, y0 = MARGIN_LEFT, MARGIN_TOP
    x1, y1 = MARGIN_LEFT + ncols * CELL, MARGIN_TOP + nrows * CELL

    img = Image.new("RGB", (W, H), "#11160F")
    draw = ImageDraw.Draw(img)
    draw.rectangle([x0, y0, x1, y1], fill="#1B2A1D", outline="#3A4A3C")

    base = bm.load_base_map()
    for fac in base["facilities"]:
        r = _rect_for(fac["cells"])
        if not r:
            continue
        fx, fy, fw, fh = r
        fac_type = fac.get("type", "")
        draw.rectangle(
            [fx + 6, fy + 6, fx + fw - 6, fy + fh - 6],
            fill=ROOF.get(fac_type, "#33403A"), outline="#57685C",
        )
        if fac_type == "runway":
            # 활주로 중앙선 — 단순 색칠 블록만으로는 활주로인지 알아보기 어렵다.
            mid_y = fy + fh / 2
            sx = fx + 16
            while sx < fx + fw - 16:
                seg_end = min(sx + 14, fx + fw - 16)
                draw.line([(sx, mid_y), (seg_end, mid_y)], fill="#C8B560", width=2)
                sx += 24
        elif fac_type == "tower":
            # 관제탑 — 몸체 위에 작은 관제실을 얹은 실루엣으로 다른 건물과 구분한다.
            cx, cy = fx + fw / 2, fy + fh / 2
            draw.rectangle([cx - 3, cy - 10, cx + 3, cy + 8], fill="#8B7BA0")
            draw.rectangle([cx - 7, cy - 15, cx + 7, cy - 9], fill="#A89BC0", outline="#57685C")

    for c in range(ncols + 1):
        x = x0 + c * CELL
        draw.line([(x, y0), (x, y1)], fill="#3E5245", width=1)
    for r_ in range(nrows + 1):
        y = y0 + r_ * CELL
        draw.line([(x0, y), (x1, y)], fill="#3E5245", width=1)

    cols_letters = base["base"]["grid"]["cols"]
    for c, label in enumerate(cols_letters):
        draw.text((x0 + c * CELL + CELL / 2 - 4, y0 - 16), label, fill="#8FB6A0")
    for r_ in range(nrows):
        draw.text((x0 - 16, y0 + r_ * CELL + CELL / 2 - 6), str(r_ + 1), fill="#8FB6A0")

    for i, marker in enumerate(markers or [], start=1):
        mx, my = marker.get("x"), marker.get("y")
        if mx is None or my is None:
            continue
        color = marker.get("color", "#E63946")
        radius = 11
        if selected_idx is not None and i - 1 == selected_idx:
            # 지금 조작(클릭 이동) 대상으로 고른 아이콘 — 강조 테두리로 구분.
            draw.ellipse(
                [mx - radius - 5, my - radius - 5, mx + radius + 5, my + radius + 5],
                outline="#F4D35E", width=3,
            )
        draw.ellipse(
            [mx - radius, my - radius, mx + radius, my + radius],
            fill=color, outline="#0D1210", width=2,
        )
        drawer = _ICON_DRAWERS.get(marker.get("emoji"))
        if drawer:
            drawer(draw, mx, my, "#0D1210")
        else:
            draw.text((mx - 3, my - 6), str(i), fill="#0D1210")

        # 같은 종류 상황이 동시에 여럿일 때 구별용 순번 배지 — 왼쪽 목록 번호와 같다.
        bx, by = mx + radius - 3, my - radius + 3
        badge_r = 7
        draw.ellipse(
            [bx - badge_r, by - badge_r, bx + badge_r, by + badge_r],
            fill="#11160F", outline="#8FB6A0", width=1,
        )
        label = str(i)
        draw.text(
            (bx - (3 if len(label) == 1 else 6), by - 5), label, fill="#E8EAEC",
        )

    return img


# "🛸"(UFO 이모지)는 무인기 프리셋의 기본 이모지지만 실제 쿼드콥터 모양과는 거리가
# 멀다. render_picker_image(PIL 래스터)는 서버에 이모지 폰트가 없으면 글자가 깨지므로
# 이 이모지가 지정된 마커만 폰트에 기대지 않는 벡터 아이콘으로 그린다. COP 화면 구성
# 탭의 SVG 지도는 브라우저가 렌더링해 이모지 폰트 걱정이 없으므로, 프리셋 편집 표와
# 항상 같은 모양이 보이도록 이런 대체 없이 이모지를 그대로 그린다.
DRONE_EMOJI = "🛸"


def _drone_icon_pil(draw, x: float, y: float, color: str) -> None:
    """쿼드콥터 실루엣(중심 동체 + 4개 암/로터)을 PIL 도형으로 그린다. 이모지 폰트에
    기대지 않는다."""
    arm = 9.5
    rotor_r = 3.3
    for dx, dy in ((-arm, -arm), (arm, -arm), (-arm, arm), (arm, arm)):
        rx, ry = x + dx, y + dy
        draw.line([(x, y), (rx, ry)], fill=color, width=2)
        draw.ellipse(
            [rx - rotor_r, ry - rotor_r, rx + rotor_r, ry + rotor_r],
            outline=color, width=1,
        )
    draw.rounded_rectangle(
        [x - 4.5, y - 3.2, x + 4.5, y + 3.2], radius=2, fill=color,
    )


# 나머지 기본 프리셋(map_icon_presets.json)도 같은 이유로 도형 아이콘을 따로 둔다.
# 프리셋 이모지와 정확히 일치하는 마커만 아이콘으로 그리고, 운용자가 새로 추가한
# 프리셋처럼 매칭되지 않는 이모지는 기존대로 순번 숫자로 표시한다(_ICON_DRAWERS 참고).
VEHICLE_EMOJI = "🚙"
INFILTRATION_EMOJI = "🚷"
FIRE_EMOJI = "🔥"
FOG_EMOJI = "🌫"


def _vehicle_icon_pil(draw, x: float, y: float, color: str) -> None:
    """차량(옆모습) 실루엣."""
    draw.rounded_rectangle([x - 8, y - 1, x + 8, y + 5], radius=2, fill=color)
    draw.rounded_rectangle([x - 4.5, y - 6, x + 4.5, y], radius=1.5, fill=color)
    for wx in (x - 4.5, x + 4.5):
        draw.ellipse([wx - 2.2, y + 3, wx + 2.2, y + 7.4], fill=color)


def _infiltration_icon_pil(draw, x: float, y: float, color: str) -> None:
    """금지 표지(원 + 대각선) — 미상인원 침투 경고."""
    r = 9
    draw.ellipse([x - r, y - r, x + r, y + r], outline=color, width=2)
    draw.line(
        [(x - r * 0.7, y + r * 0.7), (x + r * 0.7, y - r * 0.7)], fill=color, width=2,
    )


def _fire_icon_pil(draw, x: float, y: float, color: str) -> None:
    """불꽃 실루엣."""
    pts = [
        (x, y - 10), (x + 4, y - 3), (x + 6, y + 3), (x + 3, y + 8),
        (x, y + 5), (x - 3, y + 8), (x - 6, y + 3), (x - 4, y - 3),
    ]
    draw.polygon(pts, fill=color)


def _fog_icon_pil(draw, x: float, y: float, color: str) -> None:
    """안개층(짧은 가로줄 3단) 실루엣."""
    for w, oy in ((12, -5), (7, 0), (11, 5)):
        draw.line([(x - w / 2, y + oy), (x + w / 2, y + oy)], fill=color, width=2)


_ICON_DRAWERS = {
    DRONE_EMOJI: _drone_icon_pil,
    VEHICLE_EMOJI: _vehicle_icon_pil,
    INFILTRATION_EMOJI: _infiltration_icon_pil,
    FIRE_EMOJI: _fire_icon_pil,
    FOG_EMOJI: _fog_icon_pil,
}


def build_map_svg(
    active_sources: list[dict] | None = None,
    compact: bool = False,
    markers: list[dict] | None = None,
) -> str:
    W, H, _, _ = _map_dimensions()
    base = bm.load_base_map()
    grid = base["base"]["grid"]
    cols, rows = grid["cols"], grid["rows"]

    x0, y0 = MARGIN_LEFT, MARGIN_TOP
    x1, y1 = MARGIN_LEFT + len(cols) * CELL, MARGIN_TOP + rows * CELL

    p: list[str] = [
        f'<svg viewBox="0 0 {W} {H}" width="100%" xmlns="http://www.w3.org/2000/svg" '
        f'style="display:block; border-radius:6px; background:#11160F;">',
        "<defs>",
        # 지표 질감 — 옅은 얼룩 패턴
        '<pattern id="grass" width="26" height="26" patternUnits="userSpaceOnUse">'
        f'<rect width="26" height="26" fill="{TERRAIN}"/>'
        f'<circle cx="6" cy="7" r="7" fill="{TERRAIN_LIGHT}" opacity="0.5"/>'
        f'<circle cx="19" cy="18" r="5" fill="{TERRAIN_LIGHT}" opacity="0.35"/>'
        "</pattern>",
        # 포장면 질감
        '<pattern id="pave" width="14" height="14" patternUnits="userSpaceOnUse">'
        f'<rect width="14" height="14" fill="{ASPHALT}"/>'
        f'<rect width="7" height="7" fill="#33383C" opacity="0.5"/>'
        "</pattern>",
        # 건물 그림자
        '<filter id="bshadow" x="-30%" y="-30%" width="180%" height="180%">'
        '<feDropShadow dx="1.5" dy="2.5" stdDeviation="1.2" flood-color="#000" '
        'flood-opacity="0.55"/></filter>',
        "</defs>",
        f'<rect width="{W}" height="{H}" fill="#11160F"/>',
        f'<rect x="{x0}" y="{y0}" width="{x1 - x0}" height="{y1 - y0}" fill="url(#grass)"/>',
    ]

    # --- 수목·초지 얼룩 (지형감) — CCTV 점이 잘 보이도록 옅게만 ---
    for i in range(40):
        cx = x0 + _jitter(f"vx{i}", int(x1 - x0))
        cy = y0 + _jitter(f"vy{i}", int(y1 - y0))
        r = 5 + _jitter(f"vr{i}", 13)
        p.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="#1F2E20" opacity="0.3"/>'
        )

    # --- 내부 도로망 ---
    ring = [(x0 + 26, y0 + 26), (x1 - 26, y0 + 26), (x1 - 26, y1 - 26), (x0 + 26, y1 - 26)]
    p.append(
        '<polygon points="' + " ".join(f"{a},{b}" for a, b in ring) + '" fill="none" '
        f'stroke="{ROAD}" stroke-width="7" stroke-linejoin="round"/>'
    )
    p.append(
        f'<line x1="{x0 + 26}" y1="{(y0 + y1) / 2}" x2="{x1 - 26}" y2="{(y0 + y1) / 2}" '
        f'stroke="{ROAD}" stroke-width="5"/>'
    )

    # --- 활주로 · 유도로 · 계류장 ---
    facilities = {f["id"]: f for f in base["facilities"]}

    apron = facilities.get("APN")
    if apron and (r := _rect_for(apron["cells"])):
        ax, ay, aw, ah = r
        p.append(
            f'<rect x="{ax + 4}" y="{ay + 8}" width="{aw - 8}" height="{ah - 16}" '
            f'fill="{CONCRETE}" stroke="#5B6066" stroke-width="1" rx="3"/>'
        )
        for i in range(6):
            sx = ax + 14 + i * ((aw - 28) / 6)
            p.append(
                f'<rect x="{sx}" y="{ay + 16}" width="{(aw - 28) / 6 - 6}" '
                f'height="{ah - 34}" fill="none" stroke="#C8B560" stroke-width="1" '
                f'stroke-dasharray="4 4" opacity="0.6"/>'
            )

    taxi = facilities.get("TWY")
    if taxi and (r := _rect_for(taxi["cells"])):
        tx, ty, tw, th = r
        p.append(
            f'<rect x="{tx}" y="{ty + th / 2 - 13}" width="{tw}" height="26" '
            f'fill="url(#pave)" stroke="#40454A" stroke-width="1"/>'
        )
        p.append(
            f'<line x1="{tx}" y1="{ty + th / 2}" x2="{tx + tw}" y2="{ty + th / 2}" '
            f'stroke="#C8B560" stroke-width="1.6" stroke-dasharray="9 7" opacity="0.85"/>'
        )

    rwy = facilities.get("RWY")
    if rwy and (r := _rect_for(rwy["cells"])):
        rx, ry, rw, rh = r
        top = ry + rh / 2 - 21
        p.append(
            f'<rect x="{rx - 6}" y="{top - 5}" width="{rw + 12}" height="52" '
            f'fill="{TERRAIN_LIGHT}" opacity="0.5" rx="2"/>'
        )
        p.append(
            f'<rect x="{rx}" y="{top}" width="{rw}" height="42" fill="url(#pave)" '
            f'stroke="#4A5055" stroke-width="1"/>'
        )
        # 중심선
        p.append(
            f'<line x1="{rx + 30}" y1="{top + 21}" x2="{rx + rw - 30}" y2="{top + 21}" '
            f'stroke="#E8EAEC" stroke-width="2" stroke-dasharray="16 12" opacity="0.85"/>'
        )
        # 양단 접지대 스트라이프
        for side in (rx + 6, rx + rw - 26):
            for k in range(4):
                p.append(
                    f'<rect x="{side}" y="{top + 5 + k * 9}" width="20" height="5" '
                    f'fill="#E8EAEC" opacity="0.8"/>'
                )
        p.append(
            f'<text x="{rx + 30}" y="{top + 25}" fill="#E8EAEC" font-size="12" '
            f'font-weight="700" font-family="monospace" opacity="0.9">27</text>'
        )
        p.append(
            f'<text x="{rx + rw - 46}" y="{top + 25}" fill="#E8EAEC" font-size="12" '
            f'font-weight="700" font-family="monospace" opacity="0.9">09</text>'
        )

    # --- 건물 ---
    for fac in base["facilities"]:
        if fac["id"] in ("RWY", "TWY", "APN"):
            continue
        r = _rect_for(fac["cells"])
        if not r:
            continue
        fx, fy, fw, fh = r
        roof = ROOF.get(fac["type"], "#4E5A66")
        pad = 17 if fw <= CELL else 12
        bx, by = fx + pad, fy + pad + 2
        bw, bh = fw - pad * 2, fh - pad * 2 - 4
        p.append(
            f'<rect x="{bx}" y="{by}" width="{bw}" height="{bh}" fill="{roof}" '
            f'stroke="#20262B" stroke-width="1" rx="2" filter="url(#bshadow)"/>'
        )
        # 지붕 능선 — 격납고는 아치형으로 구분
        if fac["type"] == "hangar":
            p.append(
                f'<path d="M{bx + 2},{by + bh - 3} Q{bx + bw / 2},{by - 2} '
                f'{bx + bw - 2},{by + bh - 3}" fill="none" stroke="#7C8794" '
                f'stroke-width="1.4" opacity="0.8"/>'
            )
        else:
            p.append(
                f'<line x1="{bx + 3}" y1="{by + bh / 2}" x2="{bx + bw - 3}" '
                f'y2="{by + bh / 2}" stroke="#20262B" stroke-width="1" opacity="0.6"/>'
            )
        if not compact:
            p.append(
                f'<text x="{fx + fw / 2}" y="{fy + 12}" fill="#D6DEE6" font-size="9" '
                f'text-anchor="middle" font-family="sans-serif" '
                f'style="paint-order:stroke; stroke:#0D1210; stroke-width:2.5px;">'
                f'{_esc(fac["name"])}</text>'
            )

    # --- 격자 (보조 → 주 순서로 겹쳐 그림) ---
    step = CELL / SUBDIV
    for i in range(len(cols) * SUBDIV + 1):
        x = x0 + i * step
        p.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1}" stroke="#8FB6A0" '
                 f'stroke-width="0.4" opacity="0.13"/>')
    for i in range(rows * SUBDIV + 1):
        y = y0 + i * step
        p.append(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="#8FB6A0" '
                 f'stroke-width="0.4" opacity="0.13"/>')
    for c in range(len(cols) + 1):
        x = x0 + c * CELL
        p.append(f'<line x1="{x}" y1="{y0}" x2="{x}" y2="{y1}" stroke="#9FD4B4" '
                 f'stroke-width="0.9" opacity="0.3"/>')
    for r_ in range(rows + 1):
        y = y0 + r_ * CELL
        p.append(f'<line x1="{x0}" y1="{y}" x2="{x1}" y2="{y}" stroke="#9FD4B4" '
                 f'stroke-width="0.9" opacity="0.3"/>')

    for c, label in enumerate(cols):
        p.append(
            f'<text x="{x0 + c * CELL + CELL / 2}" y="{y0 - 9}" fill="#7E9C8B" '
            f'font-size="11" text-anchor="middle" font-family="monospace">{label}</text>'
        )
    for r_ in range(rows):
        p.append(
            f'<text x="{x0 - 12}" y="{y0 + r_ * CELL + CELL / 2 + 4}" fill="#7E9C8B" '
            f'font-size="11" text-anchor="middle" font-family="monospace">{r_ + 1}</text>'
        )

    # --- 외곽 울타리 ---
    p.append(
        f'<rect x="{x0 + 5}" y="{y0 + 5}" width="{x1 - x0 - 10}" height="{y1 - y0 - 10}" '
        f'fill="none" stroke="#C9A227" stroke-width="1.8" stroke-dasharray="3 5" '
        f'opacity="0.75"/>'
    )

    # --- 기존 CCTV 위치 (배경, 회색 점) ---
    active_markers = _active_camera_markers(active_sources)
    active_cells = {(x, y) for x, y, _ in active_markers}
    for x, y in _existing_camera_cells():
        if (x, y) in active_cells:
            continue  # 지금 활성화된 자리는 아래에서 빨간 점으로 다시 그린다.
        p.append(
            f'<circle cx="{x}" cy="{y}" r="3.2" fill="{EXISTING_CAMERA_COLOR}" '
            f'stroke="#0D1210" stroke-width="0.8" opacity="0.85"/>'
        )

    # --- 지금 COP 화면에 떠 있는 CCTV 위치 (빨간 점) ---
    for x, y, label in active_markers:
        p.append(
            f'<circle cx="{x}" cy="{y}" r="12" fill="{ACTIVE_CAMERA_COLOR}" fill-opacity="0.2" '
            f'stroke="{ACTIVE_CAMERA_COLOR}" stroke-width="1.4"/>'
        )
        p.append(f'<circle cx="{x}" cy="{y}" r="6.5" fill="{ACTIVE_CAMERA_COLOR}" '
                 f'stroke="#0D1210" stroke-width="1.5"/>')
        if label:
            p.append(
                f'<text x="{x}" y="{y + 3.5}" fill="#0D1210" font-size="8" font-weight="800" '
                f'text-anchor="middle" font-family="sans-serif">{_esc(label)}</text>'
            )

    # --- 실무자가 수동 배치한 아이콘 ---
    for idx, marker in enumerate(markers or [], start=1):
        mx, my = marker.get("x"), marker.get("y")
        if mx is None or my is None:
            continue
        color = marker.get("color", "#E63946")
        p.append(
            f'<circle cx="{mx}" cy="{my}" r="13" fill="{color}" fill-opacity="0.25" '
            f'stroke="{color}" stroke-width="1.6"/>'
        )
        # 프리셋 편집 표에서 설정한 이모지를 그대로 그린다 — 벡터 아이콘으로 바꿔
        # 그리면 "프리셋 아이콘 저장"에 보이는 것과 실제 지도에 뜨는 모양이 달라져
        # 헷갈린다는 피드백이 있었다. SVG는 브라우저가 렌더링하므로 서버에 이모지
        # 폰트가 없어도 항상 정상적으로 보인다(조작 탭의 PIL 래스터와 다른 점).
        p.append(
            f'<text x="{mx}" y="{my + 6}" font-size="18" '
            f'text-anchor="middle">{_esc(marker.get("emoji", "📍"))}</text>'
        )
        # 같은 종류 상황이 동시에 여럿(예: 북쪽 무인기·동쪽 무인기)일 때 구별용 순번
        # 배지 — '전장상황도 조작' 탭 목록·지도의 번호와 같다.
        p.append(
            f'<circle cx="{mx + 11}" cy="{my - 11}" r="7.5" fill="#11160F" '
            f'stroke="#8FB6A0" stroke-width="1"/>'
            f'<text x="{mx + 11}" y="{my - 8}" fill="#E8EAEC" font-size="9" '
            f'text-anchor="middle" font-family="monospace">{idx}</text>'
        )

    p.append("</svg>")
    return "".join(p)

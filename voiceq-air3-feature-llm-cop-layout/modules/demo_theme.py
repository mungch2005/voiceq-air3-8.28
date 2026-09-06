"""시연 페이지 디자인 토큰 — 발표 자료(VOICE-CUE 덱)에서 추출한 색을 그대로 쓴다.

발표 슬라이드와 시연 화면의 색이 다르면 심사위원 눈에는 다른 제품 두 개로 보인다.
그래서 값을 감으로 정하지 않고 덱의 slide XML에 실제로 쓰인 srgbClr을 빈도순으로
집계해 역할을 붙였다 (18장 전체 기준, 괄호 안이 사용 횟수):

    0A2A5E (214)  화면 바탕 네이비 — 덱의 지배색
    34435C (152)  카드/패널 표면
    3E8ED0  (80)  강조 파랑 (테두리, 선택 상태)
    6B7A90  (67)  보조 텍스트
    1B2740  (41)  가장 어두운 바탕 (비디오월 뒤)
    E8F0FA  (36)  밝은 표면
    D5DDE8  (34)  구분선
    4FD1FF  (23)  하이라이트 시안 (지금 발언 중인 화자·상황실)
    2E9E6B  (13)  정상
    D98A12   (8)  주의
    C8102E   (4)  긴급

본 앱(.streamlit/config.toml)은 중립 다크(#0B0F14) + 빨강(#E63946) 조합이라 이
팔레트와 다르다. 시연 페이지만 덱에 맞추고 본 앱은 건드리지 않는다.

한글 폰트는 덱의 minorFont(맑은 고딕)를 첫 순위로 두되, 리눅스/맥에서 발표할
경우를 대비해 대체 폰트를 함께 준다.
"""

from __future__ import annotations

# 역할 이름 -> 색. CSS 변수와 파이썬 f-string 양쪽에서 같은 값을 쓰기 위한 단일 출처.
COLORS = {
    "bg": "#0A2A5E",
    "bg_deep": "#1B2740",
    "surface": "#34435C",
    "surface_hi": "#3F5170",
    "accent": "#3E8ED0",
    "accent_bright": "#4FD1FF",
    "muted": "#6B7A90",
    "line": "#D5DDE8",
    "ice": "#E8F0FA",
    "paper": "#F3F6FB",
    "text": "#FFFFFF",
    "ok": "#2E9E6B",
    "warn": "#D98A12",
    "crit": "#C8102E",
}

FONT_STACK = "'맑은 고딕', 'Malgun Gothic', 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif"

# 발언 긴급도 -> 말풍선/카드 테두리 색. layout_renderer._URGENCY_COLOR와 같은 키를 쓴다.
URGENCY = {"긴급": COLORS["crit"], "주의": COLORS["warn"], "관찰": COLORS["accent"]}


def css() -> str:
    """시연 페이지 최상단에 한 번 주입할 <style> 블록.

    Streamlit은 st.markdown에서 <script>를 제거하므로 애니메이션은 전부 CSS로 만든다.
    """
    var_lines = "\n".join(f"  --vc-{name}: {value};" for name, value in COLORS.items())
    accent = COLORS["accent"]
    return f"""<style>
:root {{
{var_lines}
  --vc-font: {FONT_STACK};
}}
.vc-root {{
  font-family: var(--vc-font);
  color: var(--vc-text);
  background: radial-gradient(120% 90% at 78% 8%, #14406F 0%, var(--vc-bg) 45%, var(--vc-bg_deep) 100%);
  border-radius: 10px;
  padding: 12px;
}}
.vc-card {{
  background: rgba(52, 67, 92, 0.55);
  border: 1px solid rgba(213, 221, 232, 0.14);
  border-radius: 8px;
}}
.vc-card.is-speaking {{
  border-color: var(--vc-accent_bright);
  box-shadow: 0 0 0 1px var(--vc-accent_bright), 0 0 18px rgba(79, 209, 255, 0.28);
}}
/* 발표 화면은 한 화면에 꽉 차야 한다. Streamlit 기본 상단 패딩이 약 6rem이라
   그대로 두면 아래가 잘리거나 스크롤이 생긴다. */
[data-testid="stMain"] .block-container {{
  /* 3.2rem 아래로는 제목 윗부분이 떠 있는 툴바(Deploy 버튼 줄)에 가려 잘린다. */
  padding-top: 3.2rem;
  padding-bottom: 0.5rem;
}}
/* 블록 사이 기본 간격도 줄인다 — 요소가 6개뿐인데 간격이 세로를 잠식한다. */
[data-testid="stMain"] [data-testid="stVerticalBlock"] {{ gap: 0.5rem; }}

/* config.toml의 primaryColor는 본 앱의 빨강이다. 시연 페이지에서는 덱과 같은 파랑을
   쓰고, 빨강은 '긴급'에만 남긴다. */
[data-testid="stSidebar"] button[kind="primary"] {{
  background-color: {accent};
  border-color: {accent};
}}
</style>"""

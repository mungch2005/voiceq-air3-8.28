"""외부 공개 시 최소 접근 통제 — 운용 화면(app.py)과 시연 화면(demo.py)이 함께 쓴다.

예전에는 app.py 안에만 있어서, 시연 화면을 따로 띄워 공개하면 관문 없이 열렸다.
"""

from __future__ import annotations

import streamlit as st


def _is_local_access() -> bool:
    """접속이 이 컴퓨터에서 온 것인지 판별한다. 터널 경유면 Host가 외부 도메인이 된다."""
    try:
        host = str(st.context.headers.get("host", "")).split(":")[0].lower()
    except Exception:  # noqa: BLE001 — 헤더를 못 읽으면 안전한 쪽(외부로 간주)으로 판단한다.
        return False
    return host in ("localhost", "127.0.0.1", "::1", "")


def require_password() -> None:
    """외부 공개 시 최소 접근 통제.

    터널 URL은 링크만 알면 누구나 들어올 수 있고, 그 뒤에는 과금되는 API 키가 붙어 있다.
    그래서 외부 접속에는 암호를 '반드시' 요구하고, 암호가 설정되지 않았으면 아예 막는다
    (fail-closed). 로컬 접속은 암호 없이 그대로 쓴다.
    """
    expected = str(st.secrets.get("APP_PASSWORD", "") or "").strip()
    password_set = bool(expected) and "여기에" not in expected

    if _is_local_access():
        return
    if not password_set:
        st.title("VOICE-CUE")
        st.error(
            "외부 접속은 암호가 설정된 경우에만 허용됩니다.\n\n"
            "앱을 실행 중인 컴퓨터에서 `.streamlit/secrets.toml` 의 `APP_PASSWORD` 를 "
            "설정한 뒤 다시 접속하세요."
        )
        st.stop()
    if st.session_state.get("authed"):
        return

    st.title("VOICE-CUE")
    st.caption("작비스 팀 내부 시연용입니다. 접속 암호를 입력하세요.")
    entered = st.text_input("접속 암호", type="password")
    if entered:
        if entered.strip() == expected:
            st.session_state.authed = True
            st.rerun()
        else:
            st.error("암호가 일치하지 않습니다.")
    st.stop()

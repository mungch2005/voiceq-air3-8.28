"""VOICE-CUE (작비스) — 엔트리포인트. 운용 화면과 시연 화면을 한 앱에서 오간다.

    streamlit run app.py

사이드바 맨 위에서 두 화면을 전환한다.
  · 운용 화면(operate.py) — 발언 입력, 5개 탭, 플레이북·전장상황도 편집. 실무자가 쓰는 도구.
  · 시연 화면(demo.py)    — 비디오월 + 전투지휘소 평면도 + 상황실. 발표장에서 보는 화면.

한 파일로 합쳐 둔 이유: 스트림릿 클라우드는 앱 하나당 메인 파일이 하나뿐이라, 화면을
나눠 두면 배포도 둘로 늘고 Secrets도 양쪽에 따로 넣어야 한다. 여기서 나누면 배포는
그대로 하나다.

두 페이지는 각자 단독 실행도 된다(`streamlit run demo.py`). 그래서 관문은 여기서 한 번,
각 페이지에서 다시 한 번 부른다 — 이미 통과했으면 곧바로 돌아오므로 중복 비용은 없고,
어느 경로로 들어와도 관문을 지나치지 않는다.
"""

import streamlit as st

from modules import access

st.set_page_config(page_title="VOICE-CUE (작비스)", layout="wide")

access.require_password()

st.navigation(
    [
        st.Page("operate.py", title="운용 화면", icon="🛠️", default=True),
        st.Page("demo.py", title="시연 화면", icon="🖥️"),
    ]
).run()

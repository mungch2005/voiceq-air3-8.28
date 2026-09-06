"""모델이 낸 전장상황도 아이콘을 검증해서 배치하는 경로.

모델은 대상 이름(label)과 격자 칸(cell) 두 개만 낸다. 아이콘·색은 프리셋 목록에서,
좌표는 격자 이름에서 코드가 가져온다. 지어낸 대상·격자 밖 좌표를 그대로 통과시키면
지도가 조용히 망가지므로, 버려지는지 여기서 확인한다.

    python tests/test_map_markers.py
"""
import sys
import types

sys.path.insert(0, ".")


class FakeState(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e

    def __setattr__(self, k, v):
        self[k] = v


fake_st = types.SimpleNamespace(session_state=FakeState(), secrets={})
sys.modules["streamlit"] = fake_st

from modules import context_memory as cm  # noqa: E402
from modules import map_icons as mi  # noqa: E402
from modules import map_renderer as mr  # noqa: E402
from modules import prompts  # noqa: E402

cm.init_session_state()
fails: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        fails.append(message)


def markers():
    return fake_st.session_state.map_markers


def dropped():
    return fake_st.session_state.dropped_markers


def full(entries, event_id="사태1", content="드론 식별", kind="상황"):
    """map_markers를 담은 FULL 응답 한 벌."""
    return {
        "context_memory": "",
        "situation_board": [],
        "operation_log_entry": {"kind": kind, "event_id": event_id, "content": content},
        "map_markers": entries,
    }


LABEL = mi.load_presets()[0]["label"]          # "무인기"
OTHER = mi.load_presets()[2]["label"]          # "미상인원 침투"

# ---------- 1) 정상 배치 — 아이콘·색·좌표는 코드가 채운다 ----------
cm.apply_full_result(full([{"label": LABEL, "cell": "E4"}]), speaker="정보과장",
                     timestamp="14:00:00", utterance="유도로 상공 무인기")
check(len(markers()) == 1, f"마커가 1개가 아니다: {len(markers())}")
if markers():
    m = markers()[0]
    preset = mi.find_preset(LABEL)
    check(m["emoji"] == preset["emoji"], "아이콘을 프리셋에서 안 가져왔다")
    check(m["color"] == preset["color"], "색을 프리셋에서 안 가져왔다")
    check((m["x"], m["y"]) == mr.cell_center("E4"), "격자 좌표가 픽셀로 안 바뀌었다")
    check(m["cell"] == "E4", "cell이 기록되지 않았다")
check(not dropped(), f"버린 것이 없어야 하는데 있다: {dropped()}")
print(f"[1] 정상 배치 — 아이콘·색은 프리셋에서, 좌표는 격자에서 (통과)")

# ---------- 2) 같은 대상이 이동하면 새로 쌓지 않고 옮긴다 ----------
cm.apply_full_result(
    # 후속 보고이므로 kind는 "조치" — 같은 사태에 붙어야 아이콘이 옮겨진다
    full([{"label": LABEL, "cell": "B2"}], content="무인기 서측으로 이동", kind="조치"),
    speaker="정보과장", timestamp="14:01:00", utterance="무인기 서측 이동",
)
check(len(markers()) == 1, f"이동인데 마커가 늘었다: {len(markers())}")
if markers():
    check((markers()[0]["x"], markers()[0]["y"]) == mr.cell_center("B2"),
          "이동했는데 좌표가 그대로다")
print("[2] 같은 대상 재보고 -> 아이콘 이동, 개수 그대로 (통과)")

# ---------- 3) 프리셋에 없는 대상은 버린다 ----------
before = len(markers())
cm.apply_full_result(full([{"label": "레이저포탑", "cell": "C3"}], kind="조치"),
                     speaker="정보과장", timestamp="14:02:00", utterance="x")
check(len(markers()) == before, "지어낸 대상이 지도에 올라갔다")
check(any("레이저포탑" in d for d in dropped()), f"버린 이유가 안 남았다: {dropped()}")
print("[3] 프리셋에 없는 대상 -> 버림 + 이유 기록 (통과)")

# ---------- 4) 격자 밖·빠진 위치는 버린다 (위치를 지어내지 않는다) ----------
for bad in ("Z9", "E99", "", "4E"):
    before = len(markers())
    cm.apply_full_result(full([{"label": OTHER, "cell": bad}], kind="조치"),
                         speaker="정보과장", timestamp="14:03:00", utterance="x")
    check(len(markers()) == before, f"격자 밖 '{bad}'이(가) 지도에 올라갔다")
check(dropped(), "격자 밖인데 버린 기록이 없다")
print("[4] 격자 밖·빠진 위치 -> 미배치 (통과)")

# ---------- 5) map_markers가 없으면 예전처럼 발언 키워드로 찍는다 ----------
fake_st.session_state.map_markers = []
no_markers = {
    "context_memory": "", "situation_board": [],
    "operation_log_entry": {"kind": "상황", "event_id": "사태9", "content": "침투"},
}
cm.apply_full_result(no_markers, speaker="군사경찰대대장", timestamp="14:04:00",
                     utterance="남측초소에서 미상인원 침투가 확인되었습니다.")
check(markers(), "모델이 안 냈을 때 키워드 자동 배치가 동작하지 않는다")
print(f"[5] map_markers 없음 -> 키워드 자동 배치로 폴백 ({len(markers())}개) (통과)")

# ---------- 6) 프롬프트가 닫힌 목록을 실제로 알려주는가 ----------
rules = prompts.FULL_MARKER_SYSTEM_PROMPT
check("map_markers" in rules, "프롬프트에 map_markers 지시가 없다")
for preset in mi.load_presets():
    check(preset["label"] in rules, f"프롬프트에 '{preset['label']}' 대상이 안 실렸다")
check("A~J" in rules and "1~7" in rules, "프롬프트에 격자 범위가 없다")
check("map_markers" not in prompts.FULL_SYSTEM_PROMPT,
      "기본 FULL 프롬프트까지 지도 지시가 들어갔다")
print("[6] 프롬프트가 대상 목록·격자 범위를 닫힌 목록으로 제시 (통과)")

print()
if fails:
    print("실패:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("통과 — 모델 아이콘 경로: 검증·이동·폴백·프롬프트 계약 유지")

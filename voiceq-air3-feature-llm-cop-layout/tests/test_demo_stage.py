"""시연 페이지(demo.py)가 기대는 불변식 검증.

demo_* 모듈은 Streamlit을 import하지 않으므로 브라우저 없이 그냥 실행하면 된다.
    python tests/test_demo_stage.py
"""

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, ".")

from modules import demo_rooms as dr  # noqa: E402
from modules import demo_scenario as dsc  # noqa: E402
from modules import demo_stage as ds  # noqa: E402
from modules import organization as org  # noqa: E402

fails: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        fails.append(message)


# ---------- 1) 방 배정: 전원이 정확히 한 곳에 ----------
report = dr.seating_report()
seated = [t for people in report.values() for t in people]
system = [t for t in org.speaker_titles() if dr.is_system(t)]

check(len(seated) == len(set(seated)), "두 방에 중복 배정된 화자가 있다")
check(
    sorted(seated + system) == sorted(org.speaker_titles()),
    "배정된 화자 + 체계 화자가 편제 전원과 일치하지 않는다",
)
check(dr.cp_room()["kind"] == "cp", "전투지휘소를 찾지 못했다")
check(len(dr.situation_rooms()) == 4, f"상황실이 4개가 아니다: {len(dr.situation_rooms())}")
for title in org.speaker_titles():
    if not dr.is_system(title):
        check(dr.room_of(title) in dr.room_ids(), f"'{title}'이 없는 방에 배정됐다")
# 편제에 없는 이름도 화면에서 사라지면 안 된다
check(dr.room_of("존재하지 않는 직책") == dr.cp_room()["id"], "모르는 화자가 전투지휘소로 안 간다")
print(f"[방 배정] 좌석 {len(seated)}명 + 체계 {len(system)}명 = 편제 {len(org.speaker_titles())}명")
for rid, people in report.items():
    name = next(r["name"] for r in dr.rooms() if r["id"] == rid)
    print(f"    {name}: {len(people)}명" + ("  (배정된 화자 없음)" if not people else ""))


# ---------- 2) 시나리오 목록·녹음·굽기 ----------
catalog = dsc.available()
check(catalog, "재생할 시나리오가 하나도 없다")
ids = [c["id"] for c in catalog]
check(len(ids) == len(set(ids)), "시나리오 id가 겹친다")
for entry in catalog:
    sid = entry["id"]
    turns = dsc.script(sid)
    check(len(turns) == entry["turns"], f"{sid}: 목록의 턴 수와 대본이 다르다")
    check(turns, f"{sid}: 대본이 비어 있다")
    for i, turn in enumerate(turns):
        check(bool(turn.get("speaker")), f"{sid} {i + 1}번째 턴에 화자가 없다")
        check(bool(turn.get("utterance")), f"{sid} {i + 1}번째 턴에 발언이 없다")
        # 대본이 녹음을 가리키면 그 파일이 실제로 있어야 한다
        if turn.get("audio"):
            check(dsc.audio_path(sid, i) is not None,
                  f"{sid} {i + 1}번째 턴의 녹음 {turn['audio']} 이 없다")
    check(dsc.audio_path(sid, len(turns)) is None, f"{sid}: 범위 밖 턴이 녹음을 돌려준다")

    baked = dsc.load_baked(sid)
    if entry["has_bake"]:
        check(dsc.matches_script(baked, sid), f"{sid}: 굽기가 대본과 어긋난다")
        for i, bt in enumerate(baked["turns"]):
            # 재생은 이 두 값을 그대로 apply_fast_result / apply_full_result에 넘긴다
            fast = bt.get("fast") or {}
            check(isinstance(fast.get("situation"), dict),
                  f"{sid} {i + 1}턴: fast에 situation이 없다")
            full = bt.get("full") or {}
            check(isinstance(full.get("operation_log_entry"), dict),
                  f"{sid} {i + 1}턴: full에 operation_log_entry가 없다")

# 녹음 길이 — 재생 박자가 이 값을 따라간다. 못 읽으면 발언이 잘리고 겹친다.
for entry in catalog:
    sid = entry["id"]
    for i in range(entry["turns"]):
        seconds = dsc.audio_seconds(sid, i)
        if dsc.audio_path(sid, i) is None:
            check(seconds is None, f"{sid} {i + 1}턴: 녹음이 없는데 길이가 나온다")
        else:
            check(seconds is not None, f"{sid} {i + 1}턴: 녹음 길이를 못 읽었다")
            check(1.0 < (seconds or 0) < 60.0,
                  f"{sid} {i + 1}턴: 녹음 길이 {seconds}초가 말이 안 된다")
check(dsc.audio_seconds(catalog[0]["id"], 999) is None, "범위 밖 턴이 길이를 돌려준다")

sample = catalog[0]["id"]
script = dsc.script(sample)

# 저장 경로를 임시로 돌려 실제 굽기 파일을 건드리지 않는다
real_dir = dsc.DATA_DIR
with tempfile.TemporaryDirectory() as tmp:
    dsc.DATA_DIR = Path(tmp)
    (dsc.DATA_DIR / f"{sample}.json").write_text(
        json.dumps({"name": "임시", "turns": script}, ensure_ascii=False), encoding="utf-8"
    )
    check(dsc.load_baked(sample) is None, "없는 굽기 파일을 읽어 왔다")
    check(not dsc.matches_script(None, sample), "굽기가 없는데 대본과 일치한다고 한다")

    good = [
        {"speaker": t["speaker"], "utterance": t["utterance"],
         "fast": {"situation": {"type": "드론상황", "reason": "r"}},
         "full": None, "display_latency": 1.2}
        for t in script
    ]
    dsc.save_baked(good, model="m", baked_at="2026-01-01 00:00", scenario_id=sample)
    check(dsc.matches_script(dsc.load_baked(sample), sample), "왕복한 굽기가 대본과 다르다고 한다")

    dsc.save_baked(good[:-1], model="m", baked_at="x", scenario_id=sample)
    check(not dsc.matches_script(dsc.load_baked(sample), sample),
          "턴 수가 모자란 굽기를 걸러내지 못했다")

    edited = [dict(t) for t in good]
    edited[0]["utterance"] = "대본에 없는 발언"
    dsc.save_baked(edited, model="m", baked_at="x", scenario_id=sample)
    check(not dsc.matches_script(dsc.load_baked(sample), sample),
          "발언이 바뀐 굽기를 걸러내지 못했다")

    (dsc.DATA_DIR / f"{sample}.baked.json").write_text("{ 깨진 json", encoding="utf-8")
    check(dsc.load_baked(sample) is None, "깨진 굽기 파일에서 예외가 새어 나온다")
dsc.DATA_DIR = real_dir
check(dsc.DATA_DIR == real_dir, "데이터 경로를 되돌리지 못했다")
durations = [
    dsc.audio_seconds(c["id"], i)
    for c in catalog
    for i in range(c["turns"])
    if dsc.audio_path(c["id"], i)
]
if durations:
    print(f"[녹음] {len(durations)}개 — "
          f"{min(durations):.1f}~{max(durations):.1f}초 (박자가 이 길이를 따라간다)")
print(f"[시나리오] {len(catalog)}개 — " + ", ".join(
    f"{c['id']}({c['turns']}턴"
    + (",음성" if c["has_audio"] else "")
    + (",굽기" if c["has_bake"] else "") + ")"
    for c in catalog))


# ---------- 3) 무대 렌더링 ----------
cp_people = dr.occupants(dr.cp_room()["id"])
speaking = "정보과장"
check(speaking in cp_people, "테스트가 쓰는 화자가 전투지휘소에 없다")

cp_on = ds.cp_html(speaking, height="300px")
cp_off = ds.cp_html(None, height="300px")
check(cp_on.count('stroke-width="2.4"') == 1, "발언 중 좌석이 정확히 하나가 아니다")
check(cp_off.count('stroke-width="2.4"') == 0, "발언자가 없는데 켜진 좌석이 있다")
check(cp_on.count("<circle") == len(cp_people) + 1, "좌석 수가 전투지휘소 인원과 다르다")
for title in cp_people:
    check(title in cp_on, f"'{title}' 좌석이 평면도에 없다")

rooms_on = ds.rooms_grid_html("대공방어대장", height="300px")
check(rooms_on.count("is-speaking") == 1, "켜진 상황실이 정확히 하나가 아니다")
check("무인" in rooms_on, "화자가 없는 상황실이 '무인'으로 표시되지 않는다")
check(ds.rooms_grid_html(None).count("is-speaking") == 0, "발언자가 없는데 켜진 상황실이 있다")

# 발언 전 초기 상태도 깨지면 안 된다
check("발언 대기 중" in ds.subtitle_html(None, None), "초기 자막 바가 비어 있다")
check("상황 없음" in ds.header_html(), "상황 없음 상태 표시가 없다")
for key in ("드론상황", "1.4s", "14:02:07"):
    check(key in ds.header_html("드론상황", 1.4, 5, "14:02:07"), f"상태 바에 {key}가 없다")

# 발언 내용은 자막 바에만 나온다 (말풍선을 없앤 뒤의 규칙)
utterance = "무인기 2대 식별"
check(utterance not in cp_on and utterance not in rooms_on, "무대에 발언 내용이 새어 나온다")
check(utterance in ds.subtitle_html(speaking, utterance), "자막 바에 발언이 없다")

# 말하는 중(녹음이 흐르는 박자) — 화자는 나오고 발언은 아직 안 나온다
talking = ds.subtitle_html(speaking, None)
check(speaking in talking, "말하는 중인데 자막 바에 화자가 없다")
check("수신 중" in talking, "말하는 중 표시가 없다")
check("발언 대기 중" not in talking, "말하는 중인데 대기 상태로 보인다")

# 페이지 전체가 unsafe_allow_html으로 그려지므로, 사람이 넣은 문자열은 반드시 이스케이프
danger = '<script>alert(1)</script>'
rendered = [
    ds.subtitle_html(danger, danger),
    ds.header_html(danger, 1.0, 1, danger),
    ds.section_label(danger, danger),
]
for i, blob in enumerate(rendered):
    check("<script>" not in blob, f"{i}번째 렌더 출력에 스크립트 태그가 그대로 들어갔다")
    check("&lt;script&gt;" in blob, f"{i}번째 렌더 출력이 이스케이프되지 않았다")
print("[무대] 좌석/상황실 강조, 초기 상태, 발언 격리, HTML 이스케이프 확인")


print()
if fails:
    print("실패:")
    for f in fails:
        print("  " + f)
    sys.exit(1)
print("통과 — 방 배정 · 굽기 왕복 · 무대 렌더링 불변식 유지")

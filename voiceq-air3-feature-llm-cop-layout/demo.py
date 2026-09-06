"""VOICE-CUE 시연용 페이지 — 발언하는 사람과 그 결과로 바뀌는 벽면을 한 화면에.

본 앱(app.py)은 운용자가 쓰는 도구라 탭·설정·로그가 함께 있다. 이 페이지는 발표장에서
심사위원이 보는 화면이며, "누가 말했고" → "벽면이 어떻게 바뀌었는지" 두 가지만 남긴다.

  ┌──────────────────────────────────────────────┐
  │ 비디오월 2행 4열                                │
  ├───────────────────────────┬──────────────────┤
  │ 전투지휘소 (회의 테이블·좌석) │ 상황실 4개 (2×2)   │
  ├───────────────────────────┴──────────────────┤
  │ 자막 바 — 지금 발언 전문                        │
  └──────────────────────────────────────────────┘

화면 선택은 본 앱과 똑같은 경로(engine.analyze_turn → context_memory.apply_fast_result
→ playbook)를 그대로 탄다. 벽면이 2행 4열인 것만 다르며, 그것도 playbook.retile이
좌표만 다시 계산한다 — 선택 로직을 복제하면 두 화면의 판단이 갈라지기 때문이다.

실행: streamlit run demo.py
"""

import time
from pathlib import Path

import streamlit as st

from modules import access
from modules import context_memory as cm
from modules import demo_rooms as dr
from modules import demo_scenario as dsc
from modules import demo_stage as ds
from modules import demo_theme as th
from modules import layout_renderer as lr
from modules import llm_engine as engine
from modules import organization as org
from modules import playbook as pb
from modules import prompts

st.set_page_config(page_title="VOICE-CUE 시연", layout="wide")

# 이 화면도 app.py와 같은 관문을 지난다. 스트림릿 클라우드처럼 외부에 열린
# 곳에 올리면 링크만 알면 누구나 들어오고, 그 뒤에는 과금되는 API 키가 있다.
# 로컬(localhost) 접속은 예전처럼 그대로 통과한다.
access.require_password()

WALL_COLS, WALL_ROWS = pb.DEMO_GRID_COLS, pb.GRID_ROWS

cm.init_session_state()
st.session_state.setdefault("stage_speaker", "")
st.session_state.setdefault("stage_text", "")
st.session_state.setdefault("stage_voice", False)
# 다음에 재생할 대본 줄 번호. len(대본)이면 재생이 끝난 상태다.
st.session_state.setdefault("play_index", 0)
# 지금 재생 중인 시나리오와, 방금 재생한 턴의 녹음 파일 경로.
st.session_state.setdefault("scenario_id", dsc.SCENARIO_IDS[0])
st.session_state.setdefault("stage_audio", "")
# 연속 재생(영상 모드) 상태. film_phase는 "다음에 할 일"이고, film_hold는 지금 화면을
# 몇 초 보여준 뒤 그 일을 할지다. 한 발언을 voice(말한다) / caption(전사된 문장이
# 자막에 오른다) / apply(판단이 화면에 뜬다) 세 박자로 쪼개야 "발언 -> 인식 -> 표출"의
# 인과가 순서대로 보인다.
st.session_state.setdefault("film_playlist", [])
st.session_state.setdefault("film_pos", 0)
st.session_state.setdefault("film_phase", "voice")
st.session_state.setdefault("film_hold", 0.0)
# 굽기의 사태 ID -> 지금 일지의 실제 ID. 시나리오를 이어 붙일 때 쓴다.
st.session_state.setdefault("film_event_map", {})

SPEAKERS = org.speaker_titles()


def apply_turn(
    speaker: str,
    utterance: str,
    fast: dict | None,
    full: dict | None,
    display_latency: float | None = None,
    via_voice: bool = False,
) -> None:
    """판단 결과를 화면 상태에 반영한다.

    실시간 호출과 구운 결과 재생이 반드시 이 함수 하나를 거치게 해서, 발표에서 트는
    화면과 리허설에서 본 화면이 갈라지지 않게 한다.
    """
    st.session_state.stage_speaker = speaker
    st.session_state.stage_text = utterance
    st.session_state.stage_voice = via_voice

    timestamp = time.strftime("%H:%M:%S")
    if fast:
        cm.apply_fast_result(fast, utterance)
        if display_latency is not None:
            st.session_state.display_latency_history.append(display_latency)
    if full:
        cm.apply_full_result(
            full, speaker=speaker, timestamp=timestamp, utterance=utterance
        )
    st.session_state.utterance_log.append(
        {"speaker": speaker, "utterance": utterance, "timestamp": timestamp}
    )


def run_turn(speaker: str, utterance: str, via_voice: bool = False) -> dict | None:
    """실시간 LLM 호출. app.run_utterance와 같은 파이프라인이다.

    굽기가 그대로 재활용할 수 있도록 판단 결과를 담은 기록을 돌려준다. 호출이 실패하면
    None.
    """
    try:
        client_factory, model, extra_body = engine.get_runtime()
    except RuntimeError as e:
        st.error(f"LLM 호출 실패: {e}")
        return None

    summary = st.session_state.context_memory_summary
    result = engine.analyze_turn(
        client_factory=client_factory,
        model=model,
        fast_system=prompts.FAST_SYSTEM_PROMPT,
        fast_few_shot=prompts.FAST_FEW_SHOT_MESSAGES,
        fast_turn=prompts.build_fast_turn(
            context_memory_summary=summary,
            user_corrections=st.session_state.user_corrections,
            speaker_desc=org.describe_speaker(speaker),
            utterance=utterance,
            situation_list_text=pb.describe_for_llm(),
        ),
        full_system=prompts.FULL_SYSTEM_PROMPT,
        full_few_shot=prompts.FULL_FEW_SHOT_MESSAGES,
        full_turn=prompts.build_full_turn(
            context_memory_summary=summary,
            user_corrections=st.session_state.user_corrections,
            speaker_desc=org.describe_speaker(speaker),
            utterance=utterance,
            operation_log=st.session_state.operation_log,
        ),
        extra_body=extra_body,
    )

    fast = result.fast.data if result.fast else None
    full = result.full.data if result.full else None
    apply_turn(speaker, utterance, fast, full, result.display_latency, via_voice)
    for message in result.errors:
        st.warning(message)

    return {
        "speaker": speaker,
        "utterance": utterance,
        "fast": fast,
        "full": full,
        "display_latency": result.display_latency,
    }


SITUATION_KEYS = (
    "cop_layout", "situation_board", "operation_log", "utterance_log", "map_markers",
    "active_situations", "situation_type", "situation_reason", "situation_unmatched",
    "context_memory_summary", "display_latency_history", "layout_origin",
    "invented_sources", "dropped_sources",
)


def reset_situation() -> None:
    """상황 관련 상태만 비운다. 연속 재생을 처음부터 다시 시작할 때만 부른다."""
    for key in SITUATION_KEYS:
        st.session_state.pop(key, None)
    cm.init_session_state()
    st.session_state.stage_speaker = ""
    st.session_state.stage_text = ""
    st.session_state.stage_audio = ""


def speak_turn(scenario_id: str, index: int) -> None:
    """말하는 박자 — 화자를 켜고 녹음을 튼다. 자막은 아직 올리지 않는다.

    자막을 여기서 같이 띄우면 관객은 문장을 1초 만에 다 읽고 남은 6초 동안 목소리만
    듣는다. 순서가 뒤집힌 것이기도 하다 — 실제 체계는 발언이 끝나야 전사한다.
    """
    turn = dsc.script(scenario_id)[index]
    st.session_state.stage_speaker = turn["speaker"]
    st.session_state.stage_text = ""
    st.session_state.stage_voice = False
    audio = dsc.audio_path(scenario_id, index)
    st.session_state.stage_audio = str(audio) if audio else ""


def caption_turn(scenario_id: str, index: int) -> None:
    """말이 끝난 박자 — 전사된 발언을 자막 바에 올린다. 화면 구성은 아직 그대로다."""
    st.session_state.stage_text = dsc.script(scenario_id)[index]["utterance"]
    # 녹음은 이미 다 흘렀다. 지워 두지 않으면 다시 그릴 때 처음부터 또 재생된다.
    st.session_state.stage_audio = ""


def _remap_event_id(full: dict | None, scenario_id: str) -> dict | None:
    """구운 결과의 사태 ID를 지금 일지에 실제로 발급된 ID로 바꾼다.

    두 시나리오를 이어서 재생하면 둘 다 "사태1"을 쓴다. 그대로 두면 두 번째 상황의
    후속 조치가 첫 번째 사태 밑에 붙어 버린다(일지가 ID로 사태를 찾기 때문이다).
    시나리오별로 "구운 ID -> 실제 ID" 표를 들고 다니며 갈아 끼운다.
    """
    if not full:
        return full
    entry = full.get("operation_log_entry") or {}
    baked_id = str(entry.get("event_id", "") or "")
    actual = st.session_state.film_event_map.get(f"{scenario_id}:{baked_id}")
    if not baked_id or not actual or actual == baked_id:
        return full
    remapped = dict(full)
    remapped["operation_log_entry"] = {**entry, "event_id": actual}
    return remapped


def _record_event_mapping(full: dict | None, scenario_id: str) -> None:
    """새 사태가 만들어졌으면 구운 ID와 실제 ID를 짝지어 둔다."""
    entry = (full or {}).get("operation_log_entry") or {}
    if str(entry.get("kind", "")).strip() != "상황":
        return
    baked_id = str(entry.get("event_id", "") or "")
    log = st.session_state.operation_log
    if baked_id and log:
        st.session_state.film_event_map[f"{scenario_id}:{baked_id}"] = log[-1]["event_id"]


def apply_verdict(scenario_id: str, index: int, use_baked: bool) -> None:
    """판단을 화면에 올리는 박자 — 여기서 벽면이 바뀐다."""
    turn = dsc.script(scenario_id)[index]
    if use_baked:
        baked_turn = (dsc.load_baked(scenario_id) or {})["turns"][index]
        full = baked_turn.get("full")
        apply_turn(
            baked_turn["speaker"],
            baked_turn["utterance"],
            baked_turn.get("fast"),
            _remap_event_id(full, scenario_id),
            baked_turn.get("display_latency"),
        )
        _record_event_mapping(full, scenario_id)
    else:
        run_turn(turn["speaker"], turn["utterance"])


def play_scripted_turn(scenario_id: str, index: int, use_baked: bool) -> None:
    """대본의 한 줄을 재생한다. 구운 결과가 있으면 LLM을 부르지 않는다.

    그 턴의 녹음 파일 경로를 상태에 남겨, 다음 그리기에서 사이드바가 틀어 준다.
    """
    turn = dsc.script(scenario_id)[index]
    if use_baked:
        baked_turn = (dsc.load_baked(scenario_id) or {})["turns"][index]
        apply_turn(
            baked_turn["speaker"],
            baked_turn["utterance"],
            baked_turn.get("fast"),
            baked_turn.get("full"),
            baked_turn.get("display_latency"),
        )
    else:
        run_turn(turn["speaker"], turn["utterance"])

    audio = dsc.audio_path(scenario_id, index)
    st.session_state.stage_audio = str(audio) if audio else ""


# 각 박자를 화면에 몇 초 두는지. 한 발언은 세 박자로 간다 —
#   voice   말한다      (화자 강조 + 녹음, 자막 없음)
#   caption 말이 끝난다  (전사된 문장이 자막 바에 오르고 상태 바가 "분석 중"이 된다)
#   apply   판단이 뜬다  (벽면이 바뀐다)
# 세 박자로 쪼개야 "발언 -> 인식 -> 표출"의 인과가 관객 눈에 순서대로 보인다.
BEAT_SECONDS = {
    "voice": 2.0,     # 녹음이 없을 때만 쓰는 값 — 있으면 녹음 길이를 따른다
    "caption": 2.0,   # 자막을 읽는 시간
    "apply": 3.0,     # 바뀐 벽면 감상
    "ending": 4.6,    # 시나리오가 바뀌기 전 한 박자 쉼
}

# 녹음 끝과 자막 사이의 여유. 0이면 마지막 음절이 남아 있는데 자막이 올라온다.
VOICE_GRACE = 0.25


def voice_hold(scenario_id: str, index: int) -> float:
    """말하는 박자를 얼마나 둘지 — 그 녹음 길이만큼이다.

    녹음은 4~9초로 제각각이라 고정 초로는 맞출 수 없다. 짧게 잡으면 말이 끝나기 전에
    자막과 다음 발언이 밀고 들어온다.
    """
    seconds = dsc.audio_seconds(scenario_id, index)
    if seconds is None:
        return BEAT_SECONDS["voice"]
    return seconds + VOICE_GRACE


def start_film(playlist: list[str]) -> None:
    """연속 재생 시작. 첫 시나리오의 첫 발언부터 곧바로 시작한다.

    상황을 비우는 것은 여기 한 번뿐이다. 시나리오와 시나리오 사이에서는 비우지 않는다.
    """
    reset_situation()
    st.session_state.film_event_map = {}
    st.session_state.film_playlist = playlist
    st.session_state.film_pos = 0
    st.session_state.scenario_id = playlist[0]
    st.session_state.play_index = 0
    st.session_state.film_phase = "voice"
    st.session_state.film_hold = 0.0
    st.session_state.stage_auto = True


def advance_film() -> None:
    """박자 하나를 진행한다. 화면은 이미 그려진 뒤이므로 여기서 상태만 바꾼다."""
    ss = st.session_state
    playlist = ss.film_playlist or [ss.scenario_id]
    scenario_id = playlist[min(ss.film_pos, len(playlist) - 1)]
    turns = dsc.script(scenario_id)
    use_baked = ss.get("stage_use_baked", False)

    if ss.film_phase == "voice":
        speak_turn(scenario_id, ss.play_index)
        ss.film_phase = "caption"
        ss.film_hold = voice_hold(scenario_id, ss.play_index)
        return

    if ss.film_phase == "caption":
        caption_turn(scenario_id, ss.play_index)
        ss.film_phase = "apply"
        ss.film_hold = BEAT_SECONDS["caption"]
        return

    # "apply" — 여기서 벽면이 바뀐다
    apply_verdict(scenario_id, ss.play_index, use_baked)
    ss.play_index += 1
    if ss.play_index < len(turns):
        ss.film_phase = "voice"
        ss.film_hold = BEAT_SECONDS["apply"]
    elif ss.film_pos + 1 < len(playlist):
        # 다음 시나리오로 넘어가되 상황은 비우지 않는다. 앞 사태를 남겨 둬야 두 사태가
        # 동시에 진행되는 모습(작전상황판 2행, 벽면이 두 상황을 나눠 표출)이 나온다 —
        # 이 체계가 원래 보여주려는 것이고, 지우면 시나리오 두 개를 따로 튼 것이 된다.
        # 경계를 알리는 안내 화면은 두지 않는다. 관객에게는 새 사태가 하나 더 터진
        # 것으로 보여야 하고, "SCENARIO 2/2" 같은 카드는 짜 둔 대본임을 광고할 뿐이다.
        ss.film_pos += 1
        ss.scenario_id = playlist[ss.film_pos]
        ss.play_index = 0
        ss.film_phase = "voice"
        ss.film_hold = BEAT_SECONDS["ending"]
    else:
        ss.stage_auto = False


# ---------- 조작 (발표 중에는 접어 둔다) ----------
with st.sidebar:
    st.title("시연 조작")

    speaker = st.selectbox("화자", SPEAKERS, key="stage_pick_speaker")
    room = dr.room_of(speaker)
    room_name = next(r["name"] for r in dr.rooms() if r["id"] == room)
    st.caption(f"{org.lookup(speaker)['rank'] if org.lookup(speaker) else ''} · {room_name}")

    text = st.text_area("발언", height=90, key="stage_pick_text")
    if st.button("발언 처리", type="primary", use_container_width=True):
        if text.strip():
            with st.spinner("분석 중..."):
                run_turn(speaker, text.strip())
            st.rerun()
        else:
            st.warning("발언 내용을 입력하세요.")

    st.divider()
    st.caption("시나리오 재생")

    catalog = dsc.available()
    labels = {
        c["id"]: f"{c['name']}  ({c['turns']}턴{'·음성' if c['has_audio'] else ''})"
        for c in catalog
    }
    chosen = st.selectbox(
        "시나리오",
        [c["id"] for c in catalog],
        format_func=lambda i: labels[i],
        index=[c["id"] for c in catalog].index(st.session_state.scenario_id)
        if st.session_state.scenario_id in labels else 0,
    )
    if chosen != st.session_state.scenario_id:
        # 시나리오를 바꾸면 진행 위치와 재생 중인 녹음도 같이 되돌린다.
        st.session_state.scenario_id = chosen
        st.session_state.play_index = 0
        st.session_state.stage_audio = ""
        st.session_state.stage_auto = False
        st.rerun()

    scenario_id = st.session_state.scenario_id
    script = dsc.script(scenario_id)
    baked = dsc.load_baked(scenario_id)
    can_replay = dsc.matches_script(baked, scenario_id)
    # 구운 결과가 없거나 대본과 어긋나면 실시간 외에는 고를 것이 없다.
    sources = ["프리베이크", "실시간 LLM"] if can_replay else ["실시간 LLM"]
    source = st.selectbox("재생 소스", sources)
    # 자동 재생은 화면 맨 아래에서 돌기 때문에 이 선택을 상태로 넘겨야 한다.
    use_baked = source == "프리베이크"
    st.session_state.stage_use_baked = use_baked
    st.caption(dsc.describe(baked, scenario_id))

    idx = st.session_state.play_index
    done = idx >= len(script)
    st.progress(idx / len(script), text=f"{idx} / {len(script)} 발언")

    step_cols = st.columns(2)
    if step_cols[0].button("처음으로", use_container_width=True):
        st.session_state.play_index = 0
        st.session_state.stage_audio = ""
        st.session_state.stage_auto = False
        st.rerun()
    if step_cols[1].button(
        "다음 발언", type="primary", use_container_width=True, disabled=done
    ):
        with st.spinner(f"[{script[idx]['speaker']}] 처리 중…"):
            play_scripted_turn(scenario_id, idx, use_baked)
        st.session_state.play_index = idx + 1
        st.rerun()

    # 자동 재생은 화면 맨 아래에서 처리한다 — 이번 발언이 그려진 뒤에 쉬어야 관객이
    # 화면 변화를 볼 수 있기 때문이다.
    st.divider()
    st.caption("연속 재생 — 발표 영상용")

    # 굽기가 있는 시나리오만 이어 붙인다. 실시간 호출은 중간에 느려지거나 실패하면
    # 영상이 거기서 끊긴다.
    film_ids = [c["id"] for c in catalog if c["has_bake"]]
    running = bool(st.session_state.get("stage_auto"))

    if st.button(
        "■ 정지" if running else f"▶ 전체 연속 재생 ({len(film_ids)}개 시나리오)",
        type="primary",
        use_container_width=True,
        disabled=not film_ids,
    ):
        if running:
            st.session_state.stage_auto = False
        else:
            st.session_state.stage_use_baked = True
            start_film(film_ids)
        st.rerun()

    if st.button("▶ 이 시나리오만 연속 재생", use_container_width=True,
                 disabled=running or not can_replay):
        st.session_state.stage_use_baked = True
        start_film([scenario_id])
        st.rerun()

    if not film_ids:
        st.caption("구운 시나리오가 없어 연속 재생을 쓸 수 없습니다.")
    elif running:
        pos = st.session_state.film_pos + 1
        st.caption(f"재생 중 — {pos}/{len(st.session_state.film_playlist)}번째 시나리오. "
                   "박자마다 화면이 멈춰 있어 조작이 늦게 먹습니다.")

    audio_file = st.session_state.get("stage_audio") or ""
    if audio_file and Path(audio_file).exists():
        # 녹음은 m4a(AAC)다. 크롬·사파리·엣지는 그대로 재생하지만, AAC를 빼고 빌드한
        # 일부 리눅스 크로미움에서는 소리가 안 난다 — 그 경우에도 자막과 화면은 그대로다.
        st.audio(Path(audio_file).read_bytes(), format="audio/mp4", autoplay=True)

    st.divider()
    st.caption("굽기 — 실시간 LLM으로 한 번 돌려 판단 결과를 저장")
    if st.button("시나리오 굽기", use_container_width=True):
        st.session_state.play_index = 0
        st.session_state.stage_auto = False
        entries: list[dict] = []
        bar = st.progress(0.0)
        for i, turn in enumerate(script):
            with st.spinner(f"[{turn['speaker']}] {turn['utterance'][:22]}…"):
                record = run_turn(turn["speaker"], turn["utterance"])
            if record is None:
                st.error(f"{i + 1}번째 발언에서 호출이 실패해 굽기를 중단했습니다.")
                break
            entries.append(record)
            bar.progress((i + 1) / len(script))
        else:
            path = dsc.save_baked(
                entries,
                model=str(st.session_state.get("selected_model", "")),
                baked_at=time.strftime("%Y-%m-%d %H:%M"),
                scenario_id=scenario_id,
            )
            st.success(f"저장했습니다 — {path.name}")
        st.session_state.play_index = len(entries)

    st.divider()
    # 리허설용. LLM을 부르지 않고 플레이북만으로 벽면을 채워, 네트워크 없이 배치를
    # 확인하거나 발표 직전에 화면을 미리 세워 둘 때 쓴다.
    st.caption("리허설 — LLM 없이 벽면만 채우기")
    preview = st.selectbox("상황 유형", pb.situation_names(), key="stage_preview_pick")
    if st.button("이 상황으로 벽면 채우기", use_container_width=True):
        layout, _ = pb.build_layout(preview, st.session_state.stage_text or "")
        st.session_state.cop_layout = layout
        st.session_state.situation_type = preview
        st.rerun()

    if st.button("초기화", use_container_width=True):
        for key in ("cop_layout", "situation_board", "operation_log", "utterance_log",
                    "active_situations", "situation_type", "map_markers",
                    "stage_speaker", "stage_text", "stage_voice", "play_index",
                    "stage_audio"):
            st.session_state.pop(key, None)
        cm.init_session_state()
        st.rerun()


# ---------- 무대 ----------
st.markdown(th.css(), unsafe_allow_html=True)

cur_speaker = st.session_state.stage_speaker or None
cur_text = st.session_state.stage_text or None

latencies = st.session_state.display_latency_history
# 벽면에 올릴 패널 수 — 값과 그 이유는 playbook.DEMO_MAX_PANELS에 있다.
# 사태가 둘이어도 build_layout_multi가 상황마다 화면을 하나씩 번갈아 넣으므로,
# 고정 2개(전장상황도·작전상황판) 뒤 세 칸 안에 두 사태가 모두 들어온다.
WALL_PANEL_CAP = pb.DEMO_MAX_PANELS

wall_layout = pb.retile(st.session_state.cop_layout[:WALL_PANEL_CAP], WALL_COLS)

# "발언은 나왔고 판단은 아직" 박자 — 다음에 할 일이 apply면 지금 화면이 그 상태다.
processing = bool(st.session_state.get("stage_auto")) and st.session_state.film_phase == "apply"

st.markdown(
    ds.header_html(
        situation=st.session_state.situation_type,
        latency=latencies[-1] if latencies else None,
        panels=len(wall_layout),
        clock=time.strftime("%H:%M:%S"),
        processing=processing,
    ),
    unsafe_allow_html=True,
)

lr.render_cop_wall(
    wall_layout,
    st.session_state.situation_board,
    st.session_state.map_markers,
    cols=WALL_COLS,
    rows=WALL_ROWS,
    show_title=False,
    # 고정 높이 트랙. auto로 두면 지도 패널이 늘어나 상황실이 화면 밖으로 밀린다.
    # 한 화면(100vh)에 꽉 채우기 위한 세로 배분. 패널 자체의 min-height:150px보다
    # 작아지지 않게 max()로 묶는다.
    row_track="max(150px, 22vh)",
    # 발표 화면이므로 운용자용 안내 문구는 띄우지 않는다.
    empty_note="",
)

STAGE_HEIGHT = "31vh"

# 두 칸 모두 같은 높이의 구역 제목을 달아야 카드 위끝·아래끝이 나란히 맞는다.
BODY_HEIGHT = f"calc({STAGE_HEIGHT} - 21px)"

stage = st.columns([3, 2])
with stage[0]:
    st.markdown(
        ds.section_label("전투지휘소", f"{len(dr.occupants(dr.cp_room()['id']))}명 착석")
        + ds.cp_html(cur_speaker, height=BODY_HEIGHT),
        unsafe_allow_html=True,
    )
with stage[1]:
    st.markdown(
        ds.section_label("상황실", f"{len(dr.situation_rooms())}개소")
        + ds.rooms_grid_html(cur_speaker, height=BODY_HEIGHT),
        unsafe_allow_html=True,
    )

st.markdown(
    ds.subtitle_html(cur_speaker, cur_text, st.session_state.stage_voice),
    unsafe_allow_html=True,
)

# 연속 재생 — 지금 화면을 film_hold 만큼 보여준 뒤 다음 박자를 진행한다.
# 반드시 화면을 다 그린 뒤(스크립트 맨 아래)여야 한다. 위쪽에서 처리하면 아직 그리지도
# 않은 화면을 두고 기다리게 되어, 관객에게는 자막과 화면 전환이 동시에 일어난 것처럼
# 보인다 — 그러면 "발언 때문에 화면이 바뀌었다"는 인과가 사라진다.
if st.session_state.get("stage_auto"):
    time.sleep(st.session_state.film_hold)
    advance_film()
    st.rerun()

"""VOICE-CUE 운용 화면 — 발언 입력, COP/상황판/작전상황일지, 플레이북·전장상황도 편집.

발언(텍스트) -> OpenRouter 무료 모델 구조화 분석 -> Context Memory 갱신 -> COP/상황판/작전상황일지 자동 표출.

app.py가 st.navigation으로 띄우는 페이지다. 관문(access.require_password)은 app.py가
먼저 통과시키지만, 이 파일만 단독 실행하는 경우에도 지나도록 여기서도 부른다 —
이미 통과했으면 곧바로 돌아온다.
"""

import csv
import io
import time

import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates

from modules import access
from modules import context_memory as cm
from modules import layout_renderer as lr
from modules import map_icons as mi
from modules import map_renderer as mr
from modules import organization as org
from modules import playbook as pb
from modules import prompts
from modules import sources
from modules import stt
from modules import llm_engine as engine

st.set_page_config(page_title="VOICE-CUE (작비스)", layout="wide")


access.require_password()

cm.init_session_state()

SPEAKERS = org.speaker_titles() + ["직접입력"]


def run_utterance(speaker: str, utterance: str) -> None:
    try:
        client_factory, model, extra_body = engine.get_runtime()
    except RuntimeError as e:
        st.error(f"LLM 호출 실패: {e}")
        return

    summary = st.session_state.context_memory_summary

    fast_turn = prompts.build_fast_turn(
        context_memory_summary=summary,
        user_corrections=st.session_state.user_corrections,
        speaker_desc=org.describe_speaker(speaker),
        utterance=utterance,
        situation_list_text=pb.describe_for_llm(),
    )
    full_turn = prompts.build_full_turn(
        context_memory_summary=summary,
        user_corrections=st.session_state.user_corrections,
        speaker_desc=org.describe_speaker(speaker),
        utterance=utterance,
        operation_log=st.session_state.operation_log,
    )

    # 파인튜닝 모델은 few-shot 없이 학습했다. 그대로 예시를 보내면 학습 때 본 적 없는
    # 형태의 입력이 되고, 줄이려고 튜닝한 입력 토큰도 도로 늘어난다.
    skip_few_shot = st.session_state.get("skip_few_shot", False)

    # 화면 구성을 모델이 직접 내게 할지. 프롬프트가 달라지므로 학습한 구조와 맞춰야
    # 한다 — 배치를 학습하지 않은 모델에 이 프롬프트를 보내면 없는 화면 이름을
    # 지어내고, 그러면 context_memory가 검증에서 걸러 플레이북으로 되돌린다.
    llm_layout = st.session_state.get("llm_layout", False)
    fast_system = (
        prompts.FAST_LAYOUT_SYSTEM_PROMPT if llm_layout else prompts.FAST_SYSTEM_PROMPT
    )
    fast_all_shots = (
        prompts.FAST_LAYOUT_FEW_SHOT_MESSAGES if llm_layout
        else prompts.FAST_FEW_SHOT_MESSAGES
    )
    fast_shots = [] if skip_few_shot else fast_all_shots
    full_shots = [] if skip_few_shot else prompts.FULL_FEW_SHOT_MESSAGES

    # 전장상황도 아이콘도 모델이 낼지. 안 내면 코드가 발언 키워드로 찍는다(폴백).
    llm_markers = st.session_state.get("llm_markers", False)
    full_system = (
        prompts.FULL_MARKER_SYSTEM_PROMPT if llm_markers else prompts.FULL_SYSTEM_PROMPT
    )

    result = engine.analyze_turn(
        client_factory=client_factory,
        model=model,
        fast_system=fast_system,
        fast_few_shot=fast_shots,
        fast_turn=fast_turn,
        full_system=full_system,
        full_few_shot=full_shots,
        full_turn=full_turn,
        extra_body=extra_body,
    )

    timestamp = time.strftime("%H:%M:%S")
    if result.fast:
        cm.apply_fast_result(result.fast.data, utterance)
        st.session_state.display_latency_history.append(result.display_latency)
    if result.full:
        cm.apply_full_result(result.full.data, speaker=speaker, timestamp=timestamp, utterance=utterance)

    for message in result.errors:
        st.warning(message)

    st.session_state.utterance_log.append(
        {"speaker": speaker, "utterance": utterance, "timestamp": timestamp}
    )
    st.session_state.latency_history.append(result.total_latency)


# ---------- 사이드바 ----------
with st.sidebar:
    st.title("VOICE-CUE")
    st.caption("전투지휘소 발언 → 상황 인식 → 화면/기록 자동화 프로토타입")

    st.divider()
    st.subheader("발언 입력")
    speaker_choice = st.selectbox("화자", SPEAKERS)
    if speaker_choice == "직접입력":
        speaker_choice = st.text_input("화자명 직접 입력", value="")
    else:
        info = org.lookup(speaker_choice)
        if info:
            st.caption(
                f"{info['rank']} · {', '.join(info['domain'])} · 영향력 {info['influence']:.2f}"
            )
    utterance_text = st.text_area("발언 내용", height=100, placeholder="예: 무인기 2대 식별되었습니다.")

    if st.button("발언 처리", type="primary", use_container_width=True):
        if not speaker_choice or not utterance_text.strip():
            st.warning("화자와 발언 내용을 입력하세요.")
        else:
            with st.spinner("분석 중..."):
                run_utterance(speaker_choice, utterance_text.strip())


    st.divider()
    st.subheader("시연 시나리오 자동 재생")
    if st.button("샘플 시나리오 재생 (ORE 훈련)", use_container_width=True):
        import json
        from pathlib import Path

        scenario_path = Path(__file__).parent / "data" / "sample_dialogues" / "scenario1.json"
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        progress = st.progress(0.0, text="시나리오 재생 중...")
        for i, turn in enumerate(scenario):
            with st.spinner(f"[{turn['speaker']}] {turn['utterance']}"):
                run_utterance(turn["speaker"], turn["utterance"])
            progress.progress((i + 1) / len(scenario), text=f"{i + 1}/{len(scenario)} 처리됨")
        st.success("시나리오 재생 완료")

    st.divider()
    st.subheader("수동 보정")
    correction_text = st.text_input("판단 보정 사항 입력", placeholder="예: CCTV-3은 항상 좌측에 배치할 것")
    if st.button("보정 사항 반영", use_container_width=True):
        if correction_text.strip():
            cm.add_manual_correction(correction_text.strip())
            st.success("Context Memory에 반영되었습니다. 다음 발언부터 적용됩니다.")
        else:
            st.warning("보정 내용을 입력하세요.")

    if st.session_state.user_corrections:
        st.caption("적용 중인 보정 사항:")
        for c in st.session_state.user_corrections:
            st.caption(f"• {c}")

    st.divider()
    st.subheader("모델")

    def _on_provider_change() -> None:
        """공급자를 바꾸면 모델도 그 공급자 것으로 갈아끼운다.
        OpenRouter 모델명을 들고 OpenAI로 넘어가면 '모델 없음' 에러가 난다."""
        st.session_state.selected_model = engine.default_model_for(st.session_state.provider)
        st.session_state.model_options = []

    st.selectbox(
        "공급자",
        list(engine.PROVIDERS),
        key="provider",
        format_func=lambda p: engine.PROVIDERS[p]["label"],
        on_change=_on_provider_change,
        help="파인튜닝한 모델을 쓰려면 '직접 세운 서버'를 고릅니다. 같은 PC의 Ollama든 "
             "부대 내 vLLM이든 클라우드 엔드포인트든, secrets의 LOCAL_BASE_URL만 바꾸면 "
             "코드 수정 없이 붙습니다.",
    )

    if st.button("모델 목록 조회", use_container_width=True):
        try:
            st.session_state.model_options = engine.list_models(st.session_state.provider)
            st.success(f"{len(st.session_state.model_options)}개 조회됨")
        except Exception as e:  # noqa: BLE001 — 공급자별 예외가 제각각이라 통째로 잡는다.
            st.error(f"조회 실패: {str(e)[:200]}")

    options = st.session_state.get("model_options") or engine.candidates_for(
        st.session_state.provider
    )
    if st.session_state.get("selected_model") not in options:
        options = [st.session_state.selected_model, *options]
    st.selectbox(
        "사용 모델",
        options,
        key="selected_model",
        help="응답이 느리면 다른 모델로 바꿔 보세요. 목록은 수시로 바뀝니다.",
    )

    # 파인튜닝 모델은 few-shot 없이 학습했으므로 서빙할 때도 빼야 조건이 맞는다.
    # 모델 이름으로 기본값만 잡아 주고, 최종 판단은 운용자가 뒤집을 수 있게 둔다 —
    # 이름만 보고 자동으로 정해 버리면 다른 이름으로 서빙했을 때 조용히 어긋난다.
    finetuned_default = engine.is_finetuned(st.session_state.selected_model)
    if st.session_state.get("_finetuned_auto") != finetuned_default:
        st.session_state._finetuned_auto = finetuned_default
        st.session_state.skip_few_shot = finetuned_default

    st.checkbox(
        "파인튜닝 모델 (few-shot 생략)",
        key="skip_few_shot",
        help="finetune/ 파이프라인으로 학습한 모델은 few-shot 예시 없이 학습했습니다. "
             "체크하면 예시를 빼고 보내 입력 토큰이 FAST 20%, FULL 40% 줄어듭니다. "
             "튜닝하지 않은 모델에 체크하면 형식이 무너지므로 끄십시오.",
    )

    st.checkbox(
        "전장상황도 아이콘도 모델이 직접 (기획서 원안)",
        key="llm_markers",
        help="켜면 모델이 지도에 표시할 대상과 격자 칸(A~J×1~7)까지 냅니다. 아이콘·색은 "
             "모델이 아니라 프리셋 목록에서 가져오고, 격자 밖이거나 프리셋에 없는 대상은 "
             "버립니다. 끄면 발언 키워드로 코드가 찍습니다.",
    )

    st.checkbox(
        "화면 구성을 모델이 직접 (기획서 원안)",
        key="llm_layout",
        help="켜면 모델이 상황 유형과 함께 띄울 화면 목록까지 냅니다(기획서 원안 구조). "
             "gen_dataset.py --layout-target으로 만든 데이터로 학습한 모델에만 켜십시오. "
             "학습하지 않은 모델은 없는 화면 이름을 지어내며, 그 경우 검증에서 걸러 "
             "플레이북 배치로 자동 복귀합니다. 끄면 운용자 플레이북이 배치를 정합니다.",
    )

    st.caption(
        "폐쇄망 목표를 고려하면 시연에도 작은 모델을 쓰는 편이 좋습니다. "
        "거대 클라우드 모델로 시연해 놓고 온프레미스 12GB에서 된다고 하면 심사에서 반박당합니다."
    )

    st.divider()
    display_hist = st.session_state.display_latency_history
    if display_hist:
        st.metric(
            "화면 표출 지연",
            f"{display_hist[-1]:.2f}초",
            help="발언 종료 → 화면 구성(COP) 결정 완료까지. 심사 지표는 이 값입니다. 목표 5초 이내.",
        )
        st.caption(f"평균 {sum(display_hist) / len(display_hist):.2f}초 · {len(display_hist)}회")
    if st.session_state.latency_history:
        total = st.session_state.latency_history[-1]
        st.metric("전체 처리 완료", f"{total:.2f}초", help="일지·상황판까지 모두 생성 완료된 시각")

    if st.session_state.dropped_sources:
        st.warning(
            "카탈로그에 없어 폐기된 소스: " + ", ".join(st.session_state.dropped_sources)
        )

    st.divider()
    if st.button("상황 초기화", use_container_width=True):
        for key in (
            "context_memory_summary", "user_corrections", "utterance_log", "cop_layout",
            "situation_board", "operation_log", "latency_history",
            "display_latency_history", "dropped_sources",
            "map_markers", "_last_map_click", "voice_transcript",
            "active_situations", "situation_type", "situation_reason",
            "situation_unmatched", "layout_origin", "invented_sources",
        ):
            st.session_state.pop(key, None)
        cm.init_session_state()
        st.rerun()


# ---------- 메인 화면 ----------
st.title("전투지휘소 상황판 — VOICE-CUE")

# 음성 입력은 사이드바가 아니라 본문에 둔다. Streamlit 1.63 기준 st.audio_input을
# 사이드바에 넣으면, 녹음을 정지한 뒤 따라오는 재실행에서 사이드바가 테마 객체를 새로
# 만들고 위젯이 그걸 보고 파형 컨트롤러를 부수며 방금 녹음의 blob URL까지 해제한다.
# 업로드는 204로 성공했는데도 "An error has occurred, please try again."이 뜨는 이유다.
# 본문 영역에서는 테마 객체가 유지돼 재현되지 않는다 (3줄짜리 앱으로 확인).
if stt.is_configured():
    col_spk, col_rec = st.columns([1, 2])
    with col_spk:
        voice_speaker = st.selectbox("음성 화자", SPEAKERS, key="voice_speaker")
        if voice_speaker == "직접입력":
            voice_speaker = st.text_input("화자명 직접 입력", value="", key="voice_speaker_manual")
        else:
            voice_info = org.lookup(voice_speaker)
            if voice_info:
                st.caption(
                    f"{voice_info['rank']} · {', '.join(voice_info['domain'])} · "
                    f"영향력 {voice_info['influence']:.2f}"
                )
    with col_rec:
        # 녹음 정지 = 처리. 위젯 값이 바뀔 때(새 녹음 또는 삭제)만 콜백이 불리므로,
        # 다른 버튼을 눌러 생기는 재실행에서 같은 녹음을 또 처리하지 않는다.
        # 전사 결과를 확인/수정하는 중간 단계는 뺐다 — 인식된 문장은 아래 캡션과
        # "Context Memory / 발언 이력" 탭에서 그대로 보인다.
        def _mark_voice_pending() -> None:
            st.session_state.voice_pending = True

        audio_value = st.audio_input(
            "발언 녹음", key="voice_audio_input",
            on_change=_mark_voice_pending,
        )
        if st.session_state.pop("voice_pending", False) and audio_value is not None:
            if not voice_speaker:
                st.warning("화자를 먼저 입력하세요.")
            else:
                try:
                    with st.spinner("Whisper로 변환 중..."):
                        transcript = stt.transcribe(audio_value.getvalue())
                except RuntimeError as e:
                    st.error(str(e))
                except Exception as e:  # noqa: BLE001 — SDK/전송 계층 예외를 트레이스백 대신 메시지로 보여준다.
                    st.error(f"음성 변환 중 오류: {type(e).__name__}: {e}")
                else:
                    st.session_state.voice_transcript = transcript
                    with st.spinner("분석 중..."):
                        run_utterance(voice_speaker, transcript)
        if st.session_state.voice_transcript:
            st.caption(f"마지막 인식: {st.session_state.voice_transcript}")
else:
    st.caption(
        "⚠ OPENAI_API_KEY가 설정되지 않아 음성 입력을 쓸 수 없습니다. "
        "사이드바의 텍스트 입력을 이용하거나 .streamlit/secrets.toml에 키를 추가하세요."
    )

tab_wall, tab_book, tab_log, tab_memory, tab_map_ops = st.tabs(
    ["COP 화면 구성", "COP 플레이북", "작전상황일지", "Context Memory / 발언 이력", "전장상황도 조작"]
)

with tab_wall:
    lr.render_cop_wall(
        st.session_state.cop_layout, st.session_state.situation_board, st.session_state.map_markers
    )

with tab_book:
    st.subheader("COP 플레이북")
    st.caption(
        "상황 유형별로 어떤 화면을 어느 순서로 띄울지 정의합니다. AI는 상황 유형만 분류하고, "
        "화면 배치는 이 표를 그대로 따릅니다. 표를 고치면 즉시 반영됩니다."
    )

    slot_names = list(pb.load_playbook()["slots"])
    with st.expander(f"사용 가능한 화면 슬롯 {len(slot_names)}개 — 아래 이름을 그대로 입력하세요"):
        for name in slot_names:
            spec = pb.load_playbook()["slots"][name]
            kind = spec.get("type")
            if kind == "fixed":
                desc = sources.name_of(spec.get("source_id", ""))
            elif kind == "nearest_cctv":
                desc = "발언에 언급된 방위·시설명과 가장 관련 있는 CCTV를 자동 선택"
            elif kind == "prefix":
                desc = f"{spec.get('prefix')}* 중 발언 내용과 가장 관련 있는 것"
            else:
                desc = "지정 그룹 중 발언 내용과 가장 관련 있는 것"
            max_n = int(spec.get("max", 1) or 1)
            if kind != "fixed" and max_n > 1:
                desc += f" — 관련도 높은 순으로 최대 {max_n}개까지 (관련 있는 만큼만)"
            st.caption(f"• **{name}** — {desc}")

    edited = st.data_editor(
        pb.to_table(),
        num_rows="dynamic",
        use_container_width=True,
        key="playbook_editor",
        column_config={
            "상황 유형": st.column_config.TextColumn(width="medium"),
            "키워드": st.column_config.TextColumn(help="쉼표로 구분. 상황 분류의 단서로 쓰입니다."),
        },
    )

    problems = pb.validate_table(edited)
    if problems:
        st.error("저장 전 확인이 필요합니다:\n\n" + "\n".join(f"- {x}" for x in problems))

    c1, c2 = st.columns([1, 4])
    if c1.button("저장", type="primary", disabled=bool(problems)):
        pb.save_playbook(pb.from_table(edited))
        st.success("플레이북을 저장했습니다. 다음 발언부터 적용됩니다.")
    c2.caption("저장하면 data/cop_playbook.json 에 기록됩니다.")

    st.divider()
    st.markdown(
        "**미리보기** — 상황 유형과 예시 발언을 넣으면 실제 배치 결과를 확인할 수 있습니다."
    )
    pc1, pc2 = st.columns(2)
    preview_situation = pc1.selectbox("상황 유형", pb.situation_names())
    preview_utterance = pc2.text_input(
        "예시 발언 (선택)",
        placeholder="예: 북서방 상공에 무인기 식별",
        help="비워두면 각 슬롯의 첫 번째 후보가 선택됩니다. 방위·시설명을 넣으면 "
        "그 발언과 가장 관련 있는 CCTV가 어떻게 선택되는지 볼 수 있습니다.",
    )
    preview_layout, preview_un = pb.build_layout(preview_situation, preview_utterance.strip())
    for it in preview_layout:
        st.caption(f"{it['priority']}. **{it['position']}** — {it['name']} `{it['source_id']}`"
                   f"  ← 슬롯: {it['slot']}")
    if preview_un:
        st.warning("해석 실패: " + ", ".join(preview_un))

with tab_log:
    lr.render_operation_log(st.session_state.operation_log)
    if st.session_state.operation_log:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=["시간", "상황", "부서", "조치내용"])
        writer.writeheader()
        # 화면 표와 같은 규칙(사태당 부서 수만큼 행 분리)으로 펼쳐서 내보낸다.
        for row in lr.operation_log_rows(st.session_state.operation_log):
            writer.writerow(row)
        st.download_button(
            "작전상황일지 CSV 다운로드",
            data=buffer.getvalue().encode("utf-8-sig"),
            file_name="operation_log.csv",
            mime="text/csv",
        )

with tab_memory:
    st.subheader("Context Memory (현재 누적 요약)")
    st.caption("발언마다 AI가 자동으로 갱신하는 회의 맥락 메모입니다. 다음 발언을 판단할 때 이 내용을 그대로 참고합니다.")
    st.info(st.session_state.context_memory_summary or "아직 발언이 없습니다.")

    with st.expander("Context Memory 직접 수정 (평소엔 AI가 자동 갱신 — 필요할 때만 사용)"):
        # key를 지정하지 않는다 — key가 있는 위젯은 한 번 그려진 뒤로 value= 인자를
        # 무시하고 위젯 자신의 이전 입력만 계속 보여줘서, 발언이 들어와 AI가 요약을
        # 갱신해도 이 편집창엔 반영되지 않는 문제가 있었다(메인 기능인 자동 갱신
        # 표시가 깨져 보이는 원인이었다). key 없이 매번 value=로 최신값을 그려야
        # 편집창을 열 때마다 지금 요약을 정확히 반영한다.
        edited_memory = st.text_area(
            "Context Memory 편집", value=st.session_state.context_memory_summary, height=160,
        )
        if st.button("Context Memory 저장", key="save_memory"):
            st.session_state.context_memory_summary = edited_memory.strip()
            st.success("저장했습니다. 다음 발언부터 이 내용을 기준으로 판단합니다.")
            st.rerun()

    st.subheader("마지막 판단 원문 (LLM 응답 JSON)")
    st.caption(
        "발언 하나에 FAST(화면 구성용)·FULL(기록용) 두 번을 병렬로 호출하고, 응답은 "
        "순수 JSON만 받습니다. 아래는 방금 반영된 응답 그대로입니다 — 화면·상황판·일지가 "
        "이 JSON에서 나옵니다."
    )
    verdict = st.session_state.get("last_verdict") or {}
    if verdict.get("utterance"):
        st.caption(f"대상 발언: {verdict['utterance']}")
        col_fast, col_full = st.columns(2)
        with col_fast:
            st.markdown("**FAST — 상황 유형 (화면 표출 경로)**")
            st.json(verdict.get("fast") or {"(응답 없음)": ""}, expanded=True)
        with col_full:
            st.markdown("**FULL — 요약·상황판·일지 (기록 경로)**")
            st.json(verdict.get("full") or {"(응답 없음)": ""}, expanded=True)
    else:
        st.caption("아직 판단된 발언이 없습니다.")

    st.subheader("발언 이력")
    if st.session_state.utterance_log:
        for turn in reversed(st.session_state.utterance_log):
            weight = org.influence_of(turn["speaker"])
            st.markdown(
                f"**[{turn['timestamp']}] {turn['speaker']}** "
                f"<span style='opacity:0.5; font-size:0.8rem;'>영향력 {weight:.2f}</span>: "
                f"{turn['utterance']}",
                unsafe_allow_html=True,
            )
    else:
        st.caption("발언 이력이 없습니다.")

    st.subheader("비행단 편제")
    st.caption("화자의 직책·계급·담당분야가 AI 판단에 가중치로 반영됩니다.")
    st.code(org.org_tree_text(), language=None)

with tab_map_ops:
    st.subheader("전장상황도 실무자 조작")
    st.caption(
        "아이콘은 두 경로 중 하나로 놓입니다. 사이드바에서 '전장상황도 아이콘도 모델이 직접'을 "
        "켜면 모델이 대상과 격자 칸(A~J×1~7)을 내고, 끄면 발언에 프리셋 키워드(예: 무인기, "
        "전술차량, 침투)가 있을 때 언급된 시설명·방위로 코드가 위치를 잡습니다. 어느 쪽이든 "
        "아이콘·색은 프리셋 목록에서만 가져오며, 위치를 모르면 놓지 않습니다. 실무자는 아래에서 "
        "아이콘을 골라 정확한 위치로 미세 조정만 하면 됩니다 — 새 상황을 여기서 만들지는 않습니다."
    )

    dropped_markers = st.session_state.get("dropped_markers") or []
    if dropped_markers:
        st.warning(
            "모델이 낸 아이콘 중 다음은 검증에서 버렸습니다 — " + " · ".join(dropped_markers)
        )

    with st.expander("프리셋 아이콘 편집 (키워드가 발언에 하나라도 들어가면 자동 배치됩니다)"):
        edited_icons = st.data_editor(
            mi.to_table(),
            num_rows="dynamic",
            use_container_width=True,
            key="icon_editor",
            column_config={
                "키워드": st.column_config.TextColumn(
                    help="쉼표로 구분. 발언에 이 중 하나라도 들어 있으면 이 아이콘을 자동 배치합니다."
                ),
            },
        )
        if st.button("아이콘 저장", key="save_icons"):
            mi.save_presets(mi.from_table(edited_icons))
            st.success("프리셋을 저장했습니다.")
            st.rerun()

    markers = st.session_state.map_markers
    if not markers:
        st.info(
            "아직 자동으로 배치된 아이콘이 없습니다. 발언에 프리셋 키워드와 위치(시설명 또는 "
            "방위)가 함께 들어 있으면 이 지도에 자동으로 나타납니다."
        )
    else:
        col_map, col_side = st.columns([3, 2])

        with col_side:
            marker_labels = [
                f"{i + 1}. {m['emoji']} {m['label']} — {m['facility']} 인근"
                for i, m in enumerate(markers)
            ]
            sel_idx = st.selectbox(
                "위치를 조정할 아이콘", range(len(markers)),
                format_func=lambda i: marker_labels[i], key="map_adjust_idx",
            )
            st.caption("오른쪽 지도를 클릭하면 선택한 아이콘이 그 위치로 이동합니다.")

            st.divider()
            st.caption("자동 배치된 아이콘 (지도 위 번호와 같습니다)")
            for idx in reversed(range(len(markers))):
                marker = markers[idx]
                mc1, mc2 = st.columns([5, 1])
                mc1.markdown(
                    f"**{idx + 1}. {marker['emoji']} {marker['label']}** — "
                    f"{marker['facility']} 인근 ({marker['timestamp']})"
                )
                if mc2.button("삭제", key=f"del_marker_{idx}"):
                    markers.pop(idx)
                    st.rerun()

        with col_map:
            picker_image = mr.render_picker_image(markers, selected_idx=sel_idx)
            # width="stretch"로 컬럼 폭에 맞춰 축소 표시한다 — 원본 폭(822px)을 그대로
            # 쓰면 좁은 컬럼에서 이미지가 잘려 오른쪽 격자(I~J)가 안 보였다. 축소해도
            # 컴포넌트가 클릭 시점의 실제 표시 크기(coords["width"/"height"])를 함께
            # 돌려주므로, 그 비율로 원본 픽셀 좌표로 환산하면 지도 좌표계는 그대로 맞는다.
            coords = streamlit_image_coordinates(
                picker_image, key="map_click_target", width="stretch"
            )
            st.caption(
                "격자 눈금(A~J, 1~7)만 표시되는 단순 조정판입니다 — 실제 명칭·표출은 "
                "왼쪽 안내와 COP 화면 구성 탭의 지도에서 확인하세요."
            )
            if coords and coords != st.session_state.get("_last_map_click"):
                st.session_state._last_map_click = coords
                scale_x = picker_image.width / coords["width"]
                scale_y = picker_image.height / coords["height"]
                real_x = coords["x"] * scale_x
                real_y = coords["y"] * scale_y
                markers[sel_idx]["x"] = real_x
                markers[sel_idx]["y"] = real_y
                markers[sel_idx]["facility"] = mr.nearest_facility_name(real_x, real_y)
                st.rerun()

st.divider()
st.caption(
    "이 프로토타입의 모든 데이터는 가상 시나리오입니다. 실제 좌표/부대/작전 정보를 다루지 않습니다. "
    "CCTV PTZ 제어, 실시간 화자분리, 보안 격리 실행 등은 해커톤 시연 범위에서 제외되었으며 향후 확장 항목입니다."
)

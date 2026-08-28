"""VOICE-CUE (작비스) — 해커톤 프로토타입 메인 엔트리포인트.

발언(텍스트) -> OpenRouter 무료 모델 구조화 분석 -> Context Memory 갱신 -> COP/상황판/작전상황일지 자동 표출.
"""

import csv
import io
import time

import streamlit as st
from streamlit_image_coordinates import streamlit_image_coordinates

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


require_password()

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

    result = engine.analyze_turn(
        client_factory=client_factory,
        model=model,
        fast_system=prompts.FAST_SYSTEM_PROMPT,
        fast_few_shot=prompts.FAST_FEW_SHOT_MESSAGES,
        fast_turn=fast_turn,
        full_system=prompts.FULL_SYSTEM_PROMPT,
        full_few_shot=prompts.FULL_FEW_SHOT_MESSAGES,
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
    st.subheader("음성 입력 (Whisper)")
    if not stt.is_configured():
        st.caption(
            "⚠ OPENAI_API_KEY가 설정되지 않아 음성 입력을 쓸 수 없습니다. "
            "위 텍스트 입력을 이용하거나 .streamlit/secrets.toml에 키를 추가하세요."
        )
    else:
        voice_speaker = st.selectbox("화자", SPEAKERS, key="voice_speaker")
        if voice_speaker == "직접입력":
            voice_speaker = st.text_input("화자명 직접 입력", value="", key="voice_speaker_manual")
        else:
            voice_info = org.lookup(voice_speaker)
            if voice_info:
                st.caption(
                    f"{voice_info['rank']} · {', '.join(voice_info['domain'])} · "
                    f"영향력 {voice_info['influence']:.2f}"
                )

        audio_value = st.audio_input("발언 녹음", key="voice_audio_input")
        if audio_value is not None and st.button(
            "음성 → 텍스트 변환", use_container_width=True, key="transcribe_btn"
        ):
            with st.spinner("Whisper로 변환 중..."):
                try:
                    st.session_state.voice_transcript = stt.transcribe(audio_value.getvalue())
                except RuntimeError as e:
                    st.error(str(e))

        if st.session_state.voice_transcript:
            edited_transcript = st.text_area(
                "변환된 발언 (필요하면 수정 후 처리)",
                value=st.session_state.voice_transcript,
                key="voice_transcript_edit",
                height=100,
            )
            if st.button(
                "음성 발언 처리", type="primary", use_container_width=True, key="process_voice_btn"
            ):
                if not voice_speaker or not edited_transcript.strip():
                    st.warning("화자와 발언 내용을 확인하세요.")
                else:
                    with st.spinner("분석 중..."):
                        run_utterance(voice_speaker, edited_transcript.strip())
                    st.session_state.voice_transcript = ""
                    st.rerun()

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
        help="폐쇄망 전환 시 '로컬 서버'로 바꾸면 됩니다. 코드 수정 없이 base_url만 바뀝니다.",
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
        ):
            st.session_state.pop(key, None)
        cm.init_session_state()
        st.rerun()


# ---------- 메인 화면 ----------
st.title("전투지휘소 상황판 — VOICE-CUE")

tab_wall, tab_book, tab_log, tab_memory, tab_map_ops = st.tabs(
    ["COP 화면 구성", "COP 플레이북", "작전상황일지", "Context Memory / 발언 이력", "전장상황도 조작"]
)

with tab_wall:
    if st.session_state.situation_type:
        cols = st.columns([2, 4])
        cols[0].metric("판정된 상황 유형", st.session_state.situation_type)
        if st.session_state.situation_reason:
            cols[1].caption(f"판단 근거: {st.session_state.situation_reason}")
        if st.session_state.situation_unmatched:
            st.warning(
                f"모델이 낸 유형 '{st.session_state.situation_unmatched}' 은 플레이북에 없어 "
                "'기타 상황'으로 처리했습니다. 필요하면 플레이북 탭에서 추가하세요."
            )

    lr.render_cop_wall(
        st.session_state.cop_layout, st.session_state.situation_board, st.session_state.map_markers
    )
    st.caption(
        "1번 '비행단 전장상황도'는 항상 고정 표시됩니다. 지도 위 점은 지금 화면에 떠 있는 "
        "CCTV의 위치이며, 숫자는 해당 화면의 순번과 같습니다. 무인기·차량 등 발언에서 "
        "언급된 위치는 이모지 아이콘으로 같은 지도에 함께 표시되며, '전장상황도 조작' "
        "탭에서 위치를 미세 조정할 수 있습니다."
    )

    if st.session_state.dropped_sources:
        st.warning("해석하지 못한 플레이북 슬롯: " + ", ".join(st.session_state.dropped_sources))

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
    st.info(st.session_state.context_memory_summary or "아직 발언이 없습니다.")

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
        "발언에 프리셋 키워드(예: 무인기, 전술차량, 침투)가 들어 있으면 AI가 언급된 시설명·"
        "방위로 위치를 잡아 전장상황도에 자동으로 아이콘을 배치합니다. 한 발언에 여러 아이콘이 "
        "동시에 뜰 수 있습니다. 실무자는 아래에서 아이콘을 골라 정확한 위치로 미세 조정만 "
        "하면 됩니다 — 새 상황을 여기서 만들지는 않습니다."
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
            picker_image = mr.render_picker_image(markers)
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

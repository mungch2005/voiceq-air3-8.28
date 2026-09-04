@HARNESS.md

# CLAUDE.md — VOICE-CUE 해커톤 프로토타입 개발 가이드

이 문서는 Claude Code가 이 저장소에서 작업할 때 참고하는 프로젝트 컨텍스트입니다.
"제8회 공군 창의·혁신 아이디어 공모 해커톤" 기획서(작비스 / VOICE-CUE)를 기반으로 하되,
**해커톤 시연 가능한 프로토타입**을 목표로 범위를 재조정했습니다. 원 기획서를 그대로
구현하는 것이 목표가 아니라, 핵심 가치(발언 → 상황 인식 → 화면/기록 자동화)를 짧은 시간에
"보여줄 수 있는" 형태로 만드는 것이 목표입니다.

이 문서는 현재 저장소 구현 상태를 반영합니다 (최종 갱신: 2026-08-28). 코드가 문서보다
앞서 나가는 경우가 잦으므로, 세부 동작이 궁금하면 항상 해당 모듈의 코드/주석을 1차
근거로 삼으세요 — 특히 `modules/llm_engine.py`, `modules/playbook.py`, `modules/prompts.py`
상단 docstring에 설계 의도가 상세히 적혀 있습니다.

---

## 1. 프로젝트 한 줄 요약

전투지휘소 회의 발언(텍스트, 화자 선택)을 실시간으로 분석해서
① 작전상황일지(MSEL)를 자동 생성하고, ② 지금 봐야 할 화면 구성(COP 레이아웃)을
운용자가 만든 플레이북 기반으로 자동 결정해, ③ 전장상황도(SVG 지도)에 관련 위치
아이콘까지 자동 배치하는 Streamlit 대시보드 프로토타입.

---

## 2. 원안 대비 단순화 결정사항 (중요)

해커톤 시간 제약을 고려해 아래와 같이 범위를 조정했습니다.

| 항목 | 원 기획서 | 프로토타입 단순화 | 구현 상태 |
|---|---|---|---|
| STT/화자분리 | 실시간 스트리밍 ASR + 화자분리 (GPU 8GB) | 화자 구분은 "화자 선택 드롭다운"(편제 기반)으로 대체. 음성은 실시간 스트리밍이 아니라 녹음 후 Whisper API 일괄 전사 | **구현 완료.** 텍스트 입력(기본)과 음성 녹음 입력(Whisper, 별도 UI)이 둘 다 존재 — 하나가 다른 하나를 대체하지 않음 (`app.py`, `modules/stt.py`) |
| 판단/생성 모델 | 한국어 특화 sLLM 파인튜닝(LoRA, 4bit) | 시연은 OpenRouter 무료 모델(`:free`) + 프롬프트 엔지니어링 + Few-shot | **구현 완료.** 공급자를 OpenRouter/OpenAI/로컬(Ollama·vLLM) 중 사이드바에서 전환 가능 (`modules/llm_engine.py`). **원안대로의 LoRA 파인튜닝도 `finetune/`에 별도 구현**되어, 학습한 모델을 로컬 서버로 서빙하면 앱이 그대로 붙는다 (`finetune/README.md` 8절) |
| CCTV PTZ 제어 | YOLO + REST API로 실제 카메라 제어 (GPU 24GB) | 판단 결과를 UI 텍스트/아이콘으로만 표시, 실제 하드웨어 연동 없음 | **구현 완료** (플레이북 기반 화면 선택 로직으로) |
| 보안 격리 (Docker+Seccomp) | 코드 생성 후 격리 실행 | 프로토타입에서 제외 | 해당 없음 (프로토타입은 코드 생성/실행 기능 자체가 없음) |
| 화면 조작 (pywinauto) | 실제 GUI 자동화 | Streamlit 컴포넌트 배치 시뮬레이션 | **구현 완료.** 단, LLM이 레이아웃 JSON을 직접 내지 않음 — 아래 6절 참고 |

즉, **핵심 로직(발언 로그 → 상황 유형 분류 → 플레이북 기반 COP 레이아웃 → 상황판/일지
자동 생성 → Context Memory 갱신 → 전장상황도 아이콘 자동 배치)이 실제로 동작**하며,
물리적 제어(PTZ, 실제 Video Wall, 보안 샌드박스)와 오디오 STT만 미구현/목업 상태입니다.

---

## 3. 기술 스택

- **UI**: Streamlit (`.streamlit/config.toml`로 다크 테마)
- **LLM**: `openai` Python SDK로 OpenAI 호환 Chat Completions API 호출. 세 공급자를
  런타임에 전환 가능 (`modules/llm_engine.py`의 `PROVIDERS`):
  - `openrouter` (기본값) — 무료 티어. 무료 발급: https://openrouter.ai/keys
  - `openai` — 유료. `OPENAI_API_KEY` 필요
  - `local` — Ollama/vLLM/LM Studio 등 폐쇄망 온프레미스 목표를 위한 슬롯.
    `base_url`만 바뀌므로 코드 수정 없이 전환됨
  - 기본 모델은 `poolside/laguna-xs-2.1:free` (`DEFAULT_MODEL`). Qwen 계열 `:free` 모델은
    유료 전환되어 사용 불가하며, `openai/gpt-oss-20b:free` 같은 추론형 모델은 내부 추론에
    토큰을 다 써서 응답이 비는 경우가 있어(`reasoning.effort=low` 강제해도 느림) 기본값에서
    제외됨 — 실측치는 `llm_engine.py` 상단 주석 참고. 무료 모델 목록은 수시로 바뀌므로
    https://openrouter.ai/models?max_price=0 에서 확인 후 `DEFAULT_MODEL`/`MODEL_CANDIDATES`
    교체, 또는 사이드바 "모델 목록 조회"로 런타임에 확인
  - 발언 하나당 **두 번의 LLM 호출을 병렬(스레드)로** 던짐 — 자세한 이유는 6절 참고
- **STT**: OpenAI Whisper API (`whisper-1`, 한국어 고정) — `modules/stt.py`. 사이드바에
  텍스트 입력과 별도의 "음성 입력 (Whisper)" 섹션이 있어, 화자 선택 → `st.audio_input`으로
  녹음 → 변환된 텍스트를 확인/수정 후 처리하는 흐름. LLM 판단 경로의 공급자 선택(OpenRouter/
  OpenAI/로컬)과 무관하게 항상 OpenAI Whisper API를 쓰므로 **`OPENAI_API_KEY`가 별도로
  필요**함 (OpenRouter는 오디오 전사를 지원하지 않음). 키가 없으면 안내 문구만 보이고
  텍스트 입력 경로는 그대로 동작 (`stt.is_configured()`)
- **화면 소스/위치 카탈로그**: 하드코딩된 LLM 자유 응답이 아니라 `data/screen_sources.json`
  (270건, `tools/gen_screen_sources.py`로 생성) · `data/base_map.json`(A~J × 1~7 고정 격자)
  기반의 폐쇄 목록 + 코드 매칭 (`modules/sources.py`, `modules/base_map.py`)
- **상태 저장**: 세션 중에는 `st.session_state`. `modules/context_memory.py`에
  `persist_to_disk()`가 있어 필요 시 `data/context_memory.json` / `data/operation_log.json`로
  영속화 가능 (기본 파이프라인에서 자동 호출되지는 않음)
- **의존성 관리**: `requirements.txt` — `streamlit`, `openai`, `streamlit-image-coordinates`,
  `Pillow`
- **비밀키/접근 관리**: `.streamlit/secrets.toml` (`.gitignore` 처리됨). `OPENROUTER_API_KEY`
  외에 `APP_PASSWORD`가 있으면 외부(터널) 접속 시 암호를 요구하고, 설정 안 돼 있으면
  외부 접속을 fail-closed로 차단함 (`app.py`의 `require_password()`). 로컬(localhost) 접속은
  암호 없이 통과

---

## 4. 폴더 구조 (실제)

```
voice-cue/
├── app.py                       # Streamlit 엔트리포인트: 접근 통제, 발언 입력, 5개 탭 UI
├── requirements.txt
├── .streamlit/
│   ├── config.toml              # 다크 테마
│   └── secrets.toml             # (git에 커밋 금지) API 키 · APP_PASSWORD
├── modules/
│   ├── llm_engine.py            # 공급자 전환, FAST/FULL 병렬 호출, JSON 파싱/재시도, 지연시간 측정
│   ├── stt.py                   # OpenAI Whisper API 음성 → 텍스트 변환 (LLM 공급자 선택과 무관)
│   ├── prompts.py               # FAST/FULL 시스템 프롬프트 + few-shot 예시
│   ├── context_memory.py        # 세션 상태 초기화, LLM 결과 반영(일지 병합·마커 배치), 디스크 영속화
│   ├── playbook.py              # 상황 유형 → 화면 슬롯 정의, 슬롯을 실제 소스로 해석(text matching), 그리드 타일링
│   ├── sources.py               # 화면 소스 폐쇄 카탈로그, 지역/방위 기반 후보 선별·점수화
│   ├── organization.py          # 비행단 편제, 화자별 계급·담당분야·영향력 가중치
│   ├── base_map.py              # 고정 배치도 격자(A~J×1~7) 좌표 변환
│   ├── map_renderer.py          # base_map + sources를 위성사진 느낌 SVG로 렌더링, 클릭용 이미지 생성
│   ├── map_icons.py             # 키워드 기반 자동 아이콘 프리셋(무인기/차량/침투 등) 관리
│   └── layout_renderer.py       # COP 레이아웃/상황판/작전상황일지를 Streamlit HTML로 렌더링
├── tools/
│   └── gen_screen_sources.py    # data/screen_sources.json 생성기 (런타임 미사용, 결과만 커밋)
├── data/
│   ├── organization.json        # 편제·화자 정의
│   ├── cop_playbook.json        # 상황 유형별 화면 슬롯 정답 레이아웃 (앱에서 편집 가능)
│   ├── map_icon_presets.json    # 자동 배치 아이콘 프리셋 (앱에서 편집 가능)
│   ├── base_map.json            # 고정 기지 배치도 정의 (시설·초소·격자)
│   ├── screen_sources.json      # 화면 소스 카탈로그 270건 (생성됨, 커밋됨)
│   ├── sample_dialogues/
│   │   └── scenario1.json       # 시연용 샘플 발언 시퀀스 (ORE 훈련)
│   ├── context_memory.json      # (선택) persist_to_disk() 호출 시 생성, git 추적 안 함
│   └── operation_log.json       # (선택) persist_to_disk() 호출 시 생성, git 추적 안 함
├── finetune/                    # 파인튜닝 (기획서 3-나 1단계) — 앱 실행과 완전히 무관
│   ├── README.md                # 계획·기획서 대응표·실측 결과·학습 환경
│   ├── scenario_bank.py         # 합성 발언·정답 템플릿 (실데이터 확보 시 여기만 교체)
│   ├── gen_dataset.py           # JSONL 학습 데이터 생성. 프롬프트는 modules/prompts.py 재사용
│   ├── train_lora.py            # Qwen2.5-3B QLoRA 학습·병합 (--dry-run은 GPU 불필요)
│   ├── evaluate.py              # 기획서 3-라 품질지표 측정 + 하네스 자기검증
│   ├── kaggle_train.ipynb       # Kaggle 실행용 노트북 (Save & Run All로 완전 백그라운드 실행)
│   ├── colab_train.ipynb        # Colab T4 실행용 노트북 (세션 끊김에 취약, kaggle 우선 권장)
│   ├── requirements-train.txt   # 학습 전용 의존성 (앱 requirements.txt와 분리)
│   ├── data/                    # 생성된 JSONL (gitignore, seed 20260829로 재생성)
│   └── out/                     # 학습 산출물 (gitignore)
└── CLAUDE.md
```

텍스트 입력과 음성 입력(`modules/stt.py`)은 서로를 대체하지 않는 별개의 UI 경로이며,
둘 다 최종적으로 같은 `run_utterance()` 함수를 호출한다.

---

## 5. 데이터 모델

### 5.1 발언 입력
두 가지 경로가 있으며 둘 다 결과적으로 같은 `(speaker, utterance)` 쌍을 만들어
`run_utterance()`에 넘깁니다:
- **텍스트 입력** (기본) — 화자는 `organization.json` 기반 드롭다운(또는 "직접입력")으로
  고르고, 발언은 텍스트 영역에 입력
- **음성 입력** (별도 UI, `modules/stt.py`) — 화자를 동일하게 드롭다운으로 고른 뒤
  `st.audio_input`으로 녹음 → Whisper API로 전사 → 전사 결과를 텍스트 영역에서 확인/수정
  → 확정 버튼을 눌러야 처리됨 (오인식 텍스트가 그대로 일지에 들어가지 않도록 확인 단계를
  둠)
```json
{ "speaker": "항공작전상황담당", "utterance": "무인기 2대 식별되었습니다.", "timestamp": "14:02:07" }
```

### 5.2 화자/편제 (`data/organization.json`)
직책마다 계급·소속 부대·담당분야·영향력(0~1)을 갖고, LLM 프롬프트에 그대로 주입됩니다.
영향력 0.85 이상(단장급)은 결심으로, 0.5 이하(기타 지휘관 등)는 단순 사실 전파로 다르게
취급하도록 프롬프트 규칙(`prompts.py`의 `_COMMON_RULES`)에 명시되어 있습니다.
```json
{ "title": "항공작전과장", "rank": "중령", "unit": "OPS",
  "domain": ["항공작전", "작전통제", "상황"], "influence": 0.75 }
```

### 5.3 LLM 산출물 — FAST/FULL 두 경로로 분리 (한 번의 JSON이 아님)
화면 표출에 필요한 최소 정보(상황 유형)만 빠르게 받는 FAST 호출과, 상황판/일지/요약을
만드는 FULL 호출을 **스레드로 동시에** 던집니다. 순차 실행이면 두 지연이 더해지지만
병렬이면 느린 쪽 하나만큼만 걸리기 때문입니다. 화면은 FAST가 도착하는 즉시 갱신됩니다.

```json
// FAST — 화면 구성용. LLM은 화면을 직접 고르지 않고 상황 "유형"만 분류.
{ "situation": { "type": "드론상황", "reason": "북서방 상공 무인기 2대 식별" } }
```
```json
// FULL — 기록용
{
  "context_memory": "북서방 상공에 무인기 2대 식별, 활주로 방향 접근 중...",
  "situation_board": [
    { "rank": 1, "event": "무인기 2대 활주로 방향 접근", "urgency": "긴급" }
  ],
  "operation_log_entry": {
    "kind": "상황",            // "상황"(신규 사태) | "조치"(기존 사태 후속) | "무시"
    "event_id": "사태1",       // "조치"면 기존 사태 ID를 그대로 지목
    "content": "북서방 상공에서 무인기 2대 식별, 활주로 방향으로 접근 중."
  }
}
```
- `situation.type`은 반드시 `cop_playbook.json`에 정의된 상황 유형 이름 중 하나여야 하며,
  실제 화면 배치(`cop_layout`)는 **LLM이 아니라 코드가** 이 유형을 플레이북에 대입해
  결정합니다 (`playbook.build_layout`) — "전문가 정답 레이아웃 대비 일치율" 지표를
  구조적으로 보장하기 위한 설계입니다. 자세한 내용은 6절 참고.
- `operation_log_entry`는 발언 하나 = 사태 하나가 아니라, `kind`로 신규/후속/무관을
  분류해 같은 사태의 타임라인에 이어 붙입니다 (`context_memory._merge_operation_log_entry`).
  모델이 사태 ID 표기를 "사건2"/"상황 3" 등으로 흔들리게 내는 경우까지 정규화해서 매칭.
- JSON 강제: `response_format={"type": "json_object"}` + 프롬프트에 "순수 JSON만 출력"
  명시 + 응답에서 코드펜스/잡텍스트 제거 후 `json.loads`, 실패 시 재시도
  (`llm_engine.py`의 `_extract_json` / `call_llm`).

### 5.4 COP 플레이북 (`data/cop_playbook.json`)
상황 유형별로 어떤 화면 "슬롯"을 어느 순서로 띄울지 정의한 정답 레이아웃. 슬롯 종류:
- `fixed` — 고정 화면 하나 (예: 대공상황감시체계)
- `nearest_cctv` / `prefix` / `group` — 발언에 언급된 방위·시설명과 태그가 가장 많이
  겹치는 CCTV를 코드가 직접 검색해서 채움 (`sources.text_score`)
"비행단 전장상황도"는 상황 유형과 무관하게 항상 1순위 고정. `always_on`에 지정된 화면들이
남은 빈 자리를 채워 Video Wall에 빈 칸이 남지 않게 함. 앱의 "COP 플레이북" 탭에서
표(`st.data_editor`)로 직접 편집 가능하며 저장 시 이 파일에 반영됨.

### 5.5 전장상황도 자동 마커 (`data/map_icon_presets.json`)
발언 텍스트에 프리셋 키워드(예: "무인기", "전술차량", "침투")가 들어 있으면 해당 이모지
아이콘을 고정 배치도(`base_map.json`) 위 언급된 시설/방위 위치에 자동으로 놓거나 이동시킴
(`context_memory._auto_place_markers`). 실무자는 "전장상황도 조작" 탭에서 클릭으로 정밀
위치만 조정합니다 — 새 마커를 수동으로 만들지는 않음.

---

## 6. 핵심 파이프라인 (실제 구현)

1. **발언 입력**: 사이드바에서 화자(편제 드롭다운 또는 직접입력) + 발언 텍스트 입력,
   "음성 입력" 섹션에서 녹음 → Whisper 전사 → 확인 후 처리, 또는 "샘플 시나리오 재생"으로
   `sample_dialogues/scenario1.json`을 순차 재생 — 세 경로 모두 동일한 `run_utterance()`로
   수렴
2. **FAST/FULL 병렬 LLM 호출** (`llm_engine.analyze_turn`): 두 스레드가 각자 클라이언트를
   새로 만들어 동시에 호출 (httpx 커넥션 공유로 인한 인증 헤더 레이스 컨디션 방지)
3. **FAST 결과 반영** (`context_memory.apply_fast_result`): 상황 유형을 플레이북과
   대조 → `playbook.build_layout()`으로 실제 화면 슬롯을 해석 → `cop_layout` 확정 →
   화면 즉시 갱신. 이 지연시간이 사이드바 "화면 표출 지연" 지표(목표 5초 이내)
4. **FULL 결과 반영** (`context_memory.apply_full_result`): Context Memory 갱신,
   상황판(situation_board) 순위 정렬, 작전상황일지 병합(신규/후속 판단),
   발언 키워드 기반 전장상황도 마커 자동 배치
5. **렌더링** (`layout_renderer.py`): COP 레이아웃을 색상 블록 타일로, 상황판을 우선순위
   카드로, 작전상황일지를 사태별 타임라인 표로 표시
6. **작전상황일지 CSV 다운로드**: 사태당 부서 수만큼 행을 펼쳐서 내보냄
7. **수동 보정 UI**: 사용자가 입력한 보정 문구를 `user_corrections`에 누적, 이후 모든
   FAST/FULL 프롬프트에 최우선 반영 지시로 주입 → "실수 반복 방지" 시연 포인트
8. **모델/공급자 전환**: 사이드바에서 즉시 변경 가능, 코드 수정 불필요
9. **파인튜닝 모델 연결**: 모델명이 `voicecue-`로 시작하면(`llm_engine.is_finetuned`)
   사이드바의 "파인튜닝 모델 (few-shot 생략)"이 자동으로 켜지고, `run_utterance()`가
   few-shot 예시를 빼고 호출한다. 학습 데이터를 few-shot 없이 만들었으므로 서빙도
   같은 조건이어야 하며, 입력 토큰이 FAST 20.1%·FULL 40.4% 줄어든다

---

## 7. 로드맵 상태

| 항목 | 상태 |
|---|---|
| Streamlit 뼈대 + 텍스트 입력 → LLM 호출 → JSON 파싱 | ✅ 완료 |
| COP 레이아웃 그리드 렌더링 | ✅ 완료 (플레이북 기반, 우선순위별 동적 타일링) |
| 상황판 + 작전상황일지 UI (+ CSV 다운로드) | ✅ 완료 |
| Context Memory 누적/표시 + 수동 보정 버튼 | ✅ 완료 |
| 샘플 시나리오 자동 재생 데모 모드 | ✅ 완료 |
| 품질 지표(표출 지연시간) 실시간 표시 | ✅ 완료 (FAST/FULL 분리 측정) |
| 편제 기반 화자 영향력 가중치 | ✅ 완료 (원 기획서에는 없던 추가 구현) |
| 전장상황도(SVG 지도) + 발언 기반 자동 아이콘 배치 | ✅ 완료 (원 기획서에는 없던 추가 구현) |
| 다중 LLM 공급자 전환 (OpenRouter/OpenAI/로컬) | ✅ 완료 (원 기획서에는 없던 추가 구현) |
| 파인튜닝 모델 앱 연결 (few-shot 자동 생략) | ✅ 완료 — `llm_engine.is_finetuned` + 사이드바 체크박스 (`finetune/README.md` 8절) |
| 외부 접속 암호 보호 | ✅ 완료 (원 기획서에는 없던 추가 구현) |
| Whisper 음성 입력 STT | ✅ 완료 — 텍스트 입력과 별개의 UI, `OPENAI_API_KEY` 필요 (`modules/stt.py`) |
| 파인튜닝 학습 데이터셋 구축 (기획서 3-나 1단계 "500건 이상") | ✅ 완료 — 1,527턴 / 3,054 SFT 샘플 (`finetune/gen_dataset.py`) |
| 파인튜닝 평가 하네스 (기획서 3-라 4개 지표 / 4-다③) | ✅ 완료 — gold 자기검증 100% 통과 (`finetune/evaluate.py`) |
| sLLM LoRA 학습 (기획서 4-다②) | ✅ Kaggle T4 x2에서 Qwen2.5-1.5B 1에폭 완주 (152스텝 33분). **튜닝 효과 실측: 상황유형 정확도 45%→75%, 키워드 정확도 60.6%→84.9%** (`finetune/README.md` 4절). 개발 PC GPU(GTX 970)로는 불가하므로 `finetune/kaggle_train.ipynb`를 Save & Run All로 실행 |

---

## 8. Streamlit Cloud / 로컬 배포 체크리스트

- `requirements.txt`: `streamlit`, `openai`, `streamlit-image-coordinates`, `Pillow`
- API 키는 코드에 하드코딩하지 말고 `st.secrets[...]`로 접근
  - `OPENROUTER_API_KEY` (무료 발급: https://openrouter.ai/keys, 결제수단 불필요)
  - `OPENAI_API_KEY` — **음성 입력(Whisper)에 필요**. LLM 공급자로 OpenAI를 안 쓰더라도
    음성 입력을 쓰려면 반드시 있어야 함 (OpenRouter는 오디오 전사를 지원하지 않음). 없으면
    음성 입력 UI에 안내 문구만 뜨고 텍스트 입력은 그대로 동작
  - 필요 시 `LOCAL_BASE_URL`(로컬 서버 전환용)
  - `LLM_PROVIDER`를 명시하지 않으면 secrets에 실제로 들어있는 키를 보고 자동 선택
    (`configured_provider()`)
- **외부(터널)로 공개할 경우 `APP_PASSWORD`를 반드시 설정** — 설정 안 하면 외부 접속
  자체가 차단됨 (fail-closed). 로컬 접속(`localhost`)은 영향 없음
- `.streamlit/secrets.toml`은 `.gitignore`에 포함되어 있음 (커밋 금지 확인 완료)
- `data/context_memory.json`, `data/operation_log.json`도 `.gitignore` 처리됨 — 런타임
  생성 파일이므로 커밋 대상 아님
- 리소스 제한을 고려해 무료 모델 중에서도 작은 모델(`poolside/laguna-xs-2.1:free`)을
  기본값으로 둠 — 폐쇄망 온프레미스(4bit, GPU 10~12GB) 목표에 맞추기 위함

---

## 9. 시연 시나리오 (기획서 4-다 항 기반)

1. "ORE 훈련 중 무인기 2대, 지상 차량 3대 식별" 발언 입력 (또는 "샘플 시나리오 재생" 버튼)
2. → 상황 유형이 "드론상황"으로 분류되고, 플레이북에 정의된 화면 슬롯(대공상황감시체계,
   드론탐지체계, 해당 지역 CCTV 등)이 자동으로 채워지는 것을 시연
3. → 상황판에 우선순위(무인기 침투 긴급 1순위) 표시
4. → 작전상황일지에 "사태1"로 자동 기록됨을 보여줌
5. → 전장상황도 탭에서 무인기 이모지 마커가 언급된 방위에 자동으로 찍힌 것을 확인
6. 후속 발언("1번 무인기는 격추 대응, 2번은 추적 유지") 입력 → 같은 사태1의 "조치"로
   병합되는 것을 시연 (발언 하나 = 사태 하나가 아님을 강조)
7. 심사위원에게 강조할 포인트:
   - **"화면 구성 품질이 담당자 숙련도에 의존하지 않는다"** — LLM은 상황 분류만 하고
     실제 화면 배치는 운용자가 만든 플레이북이 결정하므로, 플레이북을 따르는 한
     전문가 정답 레이아웃과의 일치율은 정의상 100%
   - **"발언 종료 → 화면 표출까지 목표 5초 이내"** — 사이드바 "화면 표출 지연" 실측치로
     제시 (FAST/FULL 병렬화로 지연 최소화)
   - **"LLM API 비용 없이 데모 가능"** (OpenRouter 무료 티어)
   - **"코드 수정 없이 폐쇄망 온프레미스 모델로 전환 가능"** — 사이드바에서 공급자만
     바꾸면 됨 (base_url 교체만으로 Ollama/vLLM 연동)

---

## 10. 향후 확장 아이디어 (프로토타입에는 미포함)

- 실시간 스트리밍 ASR + 화자분리 모델 온프레미스 전환 (현재는 녹음 후 Whisper API 일괄
  전사이며, 실시간 스트리밍은 아님)
- 한국어 특화 sLLM(Qwen2.5 등) LoRA 파인튜닝으로 보안망 내 온프레미스 구동
- YOLO 기반 PTZ CCTV 자동 추적/전환
- Docker + Seccomp 기반 생성 코드 격리 실행 (Validation 단계)
- 타 지휘통제 환경(방공통제소, 재난상황실 등) 수평 확산

---

## 11. Claude Code 작업 시 유의사항

- 이 프로젝트는 군 작전 맥락의 데모이지만 **실제 민감 정보나 실제 좌표/영상 데이터는
  다루지 않음** — 모든 예시 데이터는 가상 시나리오로 생성 (`organization.json`,
  `base_map.json`, `screen_sources.json` 등 전부 "가상" 명시)
- LLM 응답은 반드시 JSON 파싱 실패에 대비한 예외처리 포함 (`llm_engine._extract_json`,
  재시도 로직 참고)
- 화면 배치·소스 이름은 LLM이 자유롭게 지어내지 않도록 항상 폐쇄 카탈로그(`sources.py`,
  `cop_playbook.json`)를 거치게 할 것 — 이 원칙을 깨는 변경(예: LLM이 직접 source_id를
  출력하게 하는 것)은 하지 말 것
- UI는 다크 테마 + 그리드 배치로 "Video Wall" 느낌을 내되, 과도한 커스텀 CSS보다는
  Streamlit 기본 컴포넌트 조합으로 안정성 우선
- 코드 변경 시마다 `streamlit run app.py`로 로컬 확인 후 커밋
- 모델/공급자 기본값을 바꿀 때는 `llm_engine.py`의 실측 코멘트(응답속도·JSON 안정성)를
  갱신할 것 — 심사에서 "거대 모델로 시연하고 온프레미스에서 된다고 주장" 하면 반박당함

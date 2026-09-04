# VOICE-CUE 파인튜닝 (기획서 3-나 1단계 / 4-다)

기획서가 "1단계 기반 구축"으로 요구한 **모의 데이터셋 구축·라벨링 + 폐쇄망용 sLLM
파인튜닝**을 이 디렉터리에서 다룬다. 앱 실행에는 전혀 관여하지 않으며,
`requirements.txt`(앱)와 `requirements-train.txt`(학습)는 분리돼 있다.

## 1. 기획서 요구사항 대응표

| 기획서 항목 | 요구 | 구현 | 상태 |
|---|---|---|---|
| 3-나 1단계 | 학습 데이터 500건 이상 | `gen_dataset.py` | ✅ **1,527건** 생성 |
| 4-다① 데이터 구축 | 화자·MSEL·중요도·COP JSON 라벨, JSONL | `gen_dataset.py` + `scenario_bank.py` | ✅ 4종 라벨 전부 |
| 4-다① 제약조건 | "항상 띄워야 하는 화면" 등 복합 명령문 | `scenario_bank.CONSTRAINT_TURNS` | ✅ 6종, 보정 규칙 누적까지 |
| 4-다② 모델 학습 | Qwen2.5 / Llama-3, LoRA | `train_lora.py` (QLoRA) + `kaggle_train.ipynb`/`colab_train.ipynb` | ✅ Kaggle T4 x2에서 1.5B 1에폭 완주 (33분) |
| 4-다③ 평가 | Keyword Accuracy + BLEU/ROUGE | `evaluate.py` | ✅ 자기검증 통과 |
| 3-라 4개 품질지표 | 표출지연/상위배치/편차/일지정확도 | `evaluate.py` | ✅ 3개 자동 측정 (③은 아래 참고) |
| 3-다 성능 | 4bit 양자화 2~4GB | Qwen2.5-3B nf4 ≈ 2GB | ✅ 기본값으로 채택 |

## 2. 핵심 설계 결정 — LLM은 COP JSON을 직접 출력하지 않는다

기획서 4-다①은 "화면 구성(COP) JSON 정답을 라벨링"한다고 적혀 있지만, 이 저장소는
이미 **화면 배치를 LLM이 아니라 운용자 플레이북(`data/cop_playbook.json`)이 결정**하도록
설계돼 있다 (`CLAUDE.md` 11절: 폐쇄 카탈로그를 거치지 않는 변경 금지).

그래서 학습 타깃을 이렇게 나눴다.

| | 학습 타깃 | 비고 |
|---|---|---|
| FAST | `situation.type` / `reason` | 상황 유형 분류만 |
| FULL | `context_memory`, `situation_board`, `operation_log_entry` | 기록 3종 |
| COP JSON | **학습 타깃 아님** | 상황 유형 → `playbook.build_layout()`으로 결정론적 파생 |

COP 정답 레이아웃은 `turns_*.jsonl`의 `cop_reference`에 함께 저장되어 **평가 지표 ②의
정답**으로 쓰인다. 모델이 `source_id`를 지어낼 수 없으므로 존재하지 않는 화면을 띄우는
사고가 원천 차단되고, 기획서 3-라②의 "전문가 정답 레이아웃 대비 일치율 90% 이상"은
"상황 유형을 맞혔는가" 하나로 좁혀진다.

프롬프트는 `modules/prompts.py`의 `build_fast_turn`/`build_full_turn`을 **그대로 호출**해
만든다. 문자열을 복제하지 않으므로 런타임 프롬프트가 바뀌면 학습 데이터도 자동으로 따라간다.

## 3. 실행 순서

```bash
# ① 데이터 구축 (GPU 불필요, 수 초)
python finetune/gen_dataset.py                 # 기본: 220 시나리오, seed 20260829

# ② 데이터 점검 (GPU·torch 불필요)
python finetune/train_lora.py --dry-run

# ③ 평가 하네스 자기검증 (GPU 불필요) — 전 지표 100%가 나와야 정상
python finetune/evaluate.py --backend gold
python finetune/evaluate.py --backend perturb --noise 0.3   # 지표가 실제로 하락하는지

# ④ 학습 (GPU 필요 — 끊김 없이 돌리려면 finetune/kaggle_train.ipynb, 상세는 6절.
#    Colab은 finetune/colab_train.ipynb, 상세는 5절)
pip install -r finetune/requirements-train.txt
python finetune/train_lora.py

# ⑤ 튜닝 모델 평가
python finetune/evaluate.py --backend hf \
    --model Qwen/Qwen2.5-3B-Instruct --adapter finetune/out/adapter

# ⑥ 폐쇄망 서빙용 병합
python finetune/train_lora.py --merge finetune/out/merged
```

`finetune/data/`와 `finetune/out/`은 `.gitignore` 처리돼 있다. 데이터는 시드가 고정된
결정론적 생성물이므로 커밋하지 않고 `--seed 20260829`로 언제든 동일하게 재생성한다.

## 4. 지금까지 실측한 결과

`gen_dataset.py` 기본값(220 시나리오, seed 20260829) 기준이다.

**데이터셋** — 시나리오 단위 + 상황 유형별 계층화 분할이라 같은 회의의 앞뒤 턴이
train/test에 흩어지지 않고, 11개 상황 유형이 모든 split에 존재한다.

| split | 시나리오 | 턴 | SFT 샘플 | 상황 유형 |
|---|---|---|---|---|
| train | 176 | 1,215 | 2,430 | 11 / 11 |
| valid | 22 | 133 | 266 | 11 / 11 |
| test | 22 | 179 | 358 | 11 / 11 |
| **합계** | **220** | **1,527** | **3,054** | |

일지 라벨 분포(train): 상황 242 · 조치 838 · 무시 135. "발언 하나 = 사태 하나가 아니다"를
학습시키는 것이 목적이므로 조치 비중이 높고, 잡담(무시)도 11% 포함돼 있다.

**토큰 길이** (Qwen2.5-3B-Instruct 실제 토크나이저)

| 경로 | median | p95 | max |
|---|---|---|---|
| FAST | 1,195 | 1,242 | 1,295 |
| FULL | 1,549 | 1,663 | 1,740 |

train+valid p99 = 1,681 → `--max-seq-len 2048`로 잘림 없음.

**few-shot 제거 효과** — 파인튜닝의 실익이 여기서 나온다. 학습 데이터에 few-shot 예시를
넣지 않으므로(`gen_dataset.py` 기본값), 튜닝 후에는 서빙 시에도 few-shot을 뺄 수 있다.

| 경로 | few-shot 포함 | 제거 | 입력 토큰 절감 |
|---|---|---|---|
| FAST | 1,486 | 1,188 | **20.1%** |
| FULL | 2,598 | 1,549 | **40.4%** |

기획서 3-다 "명령 생성 2초 이내"와 3-라① "표출 지연 5초 이내"에 직접 기여하는 수치다.

**첫 학습 완주 결과** — Kaggle T4 x2, DDP 2프로세스, Qwen2.5-1.5B-Instruct, 1에폭.

| 항목 | 값 |
|---|---|
| 스텝 | 152 (실효 배치 16 = 2 x 4 x 2GPU) |
| 소요 | **33분 11초** (11.6~12.2 s/it) |
| eval_loss | 0.0931 → **0.0844** |
| eval 토큰 정확도 | 0.978 → **0.980** |

같은 152스텝이 수정 전에는 5시간 50분 걸렸다(그마저 완주 못 하고 죽었다). 세 가지를 고쳐
약 11배 빨라진 것이다 — ① `torch.cuda.is_bf16_supported()`가 T4에서도 True를 돌려줘 가속
경로가 없는 에뮬레이션 bf16으로 돌던 것, ② `device_map="auto"`가 모델을 T4 두 장에 쪼개
얹어 층마다 GPU 간 전송이 생기던 것, ③ GPU 두 장을 데이터 병렬로 쓰지 않던 것.

**튜닝 전/후 지표 비교** — Kaggle T4 x2, Qwen2.5-1.5B-Instruct 1에폭, test split 40턴,
양쪽 모두 4bit 로드. 튜닝 전에만 few-shot을 켰다(튜닝 후에는 few-shot 없이도 형식을
지키는 것 자체가 성과이므로).

| 지표 | 튜닝 전 | 튜닝 후 | 변화 | 기획서 목표 |
|---|---|---|---|---|
| ② 상황유형 정확도 | 45.00% | **75.00%** | +30.00 | 90% 이상 |
| ② COP 셀 일치율 | 73.75% | **88.33%** | +14.58 | 90% 이상 |
| ④ 키워드 정확도 | 60.59% | **84.93%** | +24.34 | 90% 이상 |
| ④ 누락률 | 0.00% | **0.00%** | ±0 | 5% 미만 ✅ |
| ④ 일지 kind 정확도 | 82.50% | **92.50%** | +10.00 | — |
| ④ ROUGE-L | 54.90% | **79.21%** | +24.31 | — |
| JSON 유효율(FAST) | 100% | **100%** | ±0 | — |
| ① FAST 표출 지연 | 3.07초 | 4.77초 | +1.70 | 5초 이내 ✅ |

읽는 법:

- **파인튜닝 효과는 분명하다.** 상황유형 정확도가 45% → 75%로 올랐고, 일지 품질
  지표(키워드·ROUGE-L)도 20%p 이상 개선됐다.
- **아직 목표(90%)에는 못 미친다.** 1.5B/1에폭이라 당연한 결과이며, 3B로 올리거나
  에폭을 늘리면 더 오를 여지가 있다. 남은 오분류는 "활주로 피해 상황 → 드론상황"처럼
  화면 구성이 비슷한 유형 사이에서 주로 발생한다.
- **지연시간이 늘어난 것은 측정 조건 탓이 크다.** 4bit + HF `generate`로 잰 값이라
  실제 서빙(vLLM 등)보다 느리다. 그래도 목표인 5초 안에는 든다.
- **누락률 0%와 JSON 유효율 100%는 튜닝 전에도 달성돼 있었다.** 이 둘은 파인튜닝이
  아니라 프롬프트 설계와 `_extract_json` 재시도 로직이 잡아 주는 부분이다.

**평가 하네스 검증**

| 백엔드 | 상황유형 정확도 | COP 완전일치 | COP 셀 일치율 | 일지 kind | 키워드 정확도 | 누락률 |
|---|---|---|---|---|---|---|
| `gold` (정답 그대로) | 100% | 100% | 100% | 100% | 100% | 0% |
| `perturb --noise 0.3` | 70.4% | 75.4% | 87.9% | 73.1% | 91.7% | 9.3% |

`gold`에서 전 지표 100%가 나오므로 지표 계산식 자체는 정상이고, `perturb`에서 유의미하게
하락하므로 오류를 실제로 잡아낸다. `perturb`에서 COP 완전일치(75.4%)가 상황유형
정확도(70.4%)보다 높은 것은 정상이다 — 여러 상황 유형이 "해당 지역 cctv" 슬롯만 갖고 있어
유형을 틀려도 같은 레이아웃이 나오는 경우가 있기 때문이다. 셀 일치율이 더 높은 것도 같은
이유이며, 1순위 대형 화면(4칸)은 대부분의 유형에서 공통이라 유형을 틀려도 그 칸은 맞는다.

## 5. Colab에서 학습하기

`finetune/colab_train.ipynb`를 Colab에 올리고 **런타임 유형을 T4 GPU**로 바꾼 뒤 위에서부터 실행하면
데이터 생성 → 사전 점검 → 베이스라인 측정 → 학습 → 튜닝 후 비교까지 한 번에 진행된다.

T4는 Turing(SM 75)이라 bf16 텐서코어가 없다. 여기서 **`torch.cuda.is_bf16_supported()`를
쓰면 안 된다** — 이 함수는 `including_emulation` 기본값이 True라 T4에서도 True를 돌려주고,
그러면 가속 경로 없는 에뮬레이션 bf16으로 떨어져 몇 배 느려진다(실측: 135초/스텝).
`train_lora.py`와 `evaluate.py`는 연산 능력을 직접 보고 8.0 미만이면 fp16을 쓴다.

`--save-steps` 주기(기본 25스텝)마다 체크포인트를 남기고, 체크포인트가 있으면 자동으로
이어서 학습한다. 세션 종료 후에도 남기려면 노트북의 `USE_DRIVE = True`로 Drive에 저장한다.

| 모델 | 4bit 크기 | GPU 1장 1에폭 | GPU 2장(DDP) 1에폭 |
|---|---|---|---|
| Qwen2.5-1.5B-Instruct | ~1.1GB | 약 1h | **33분 (Kaggle T4 x2 실측)** |
| Qwen2.5-3B-Instruct | ~2.0GB | 약 2h | 약 1h (추정) |

Colab은 T4를 한 장만 주므로 위 표의 왼쪽 열에 해당한다. 두 장을 주는 Kaggle이 더 빠르다.
기획서 3-다의 "4bit 2~4GB"에 정확히 맞는 것은 3B이므로, 1.5B로 파이프라인을 완주시킨 뒤
최종 수치는 3B로 다시 돌리는 순서를 권장한다.

**Colab은 인터랙티브 세션이 브라우저 연결에 묶여 있어 자주 끊긴다.** Pro+의 "백그라운드
실행"도 2026년 기준 불안정하다는 신고가 많아([참고](https://github.com/googlecolab/colabtools/issues/5950))
믿고 쓰기 어렵다. 끊김 없이 돌리고 싶으면 아래 6절의 Kaggle 경로를 쓴다.

## 6. Kaggle에서 학습하기 — 진짜로 끊김 없이 돌리려면

`finetune/kaggle_train.ipynb`를 쓴다. Colab과 달리 Kaggle은 "Save Version → Save & Run All
(Commit)"을 누르면 그 시점 노트북이 **별도 머신에서 완전히 분리되어** 처음부터 끝까지 자동
실행된다. 브라우저를 닫아도, 컴퓨터를 꺼도 계속 돈다 — Colab의 세션-연결 모델과 근본적으로
다르다.

무료 할당량은 주당 약 30 GPU시간, 세션(커밋 실행 포함)당 최대 약 12시간, GPU는 P100(16GB) 또는
T4×2 중 선택. 시작 전 노트북 우측 Settings 패널에서 **Accelerator를 GPU로, Internet을 On으로**
바꿔야 한다(둘 다 기본값은 꺼짐/None).

쓰는 법: 1~5번 셀(GPU 확인 → 설치 → 설정 → 데이터 생성 → 사전 점검)을 먼저 인터랙티브하게
돌려 문제없는지 확인한 뒤, **Save Version → Save & Run All (Commit)**으로 나머지(베이스라인 →
학습 → 평가 → 병합)를 백그라운드로 넘긴다. Google Drive 마운트 같은 게 필요 없다 —
`/kaggle/working` 아래에 저장한 것은 커밋이 끝나면 그 버전의 Output 탭에서 그대로 받는다.

T4 두 장이 잡히면 노트북이 자동으로 데이터 병렬(DDP)로 두 장을 다 쓴다. 실효 배치는 GPU
장수와 무관하게 16으로 유지된다.

## 7. 로컬에서는 학습할 수 없다

이 저장소가 있는 개발 PC의 GPU는 **GTX 970 (4GB, Compute Capability 5.2)** 이다.

- bitsandbytes의 4bit(nf4) 커널이 Turing/Ampere 이상을 요구한다 (CC 5.2 미지원)
- bf16 연산 미지원
- VRAM 4GB로는 3B 모델 QLoRA에 부족

따라서 학습은 아래 중 하나에서 수행한다. 데이터 구축·검증·평가 하네스는 GPU 없이
로컬에서 전부 돌아가므로, 학습만 옮기면 된다.

| 환경 | VRAM | 3B QLoRA 1 epoch 예상 |
|---|---|---|
| Kaggle T4 x2 (무료) | 16GB x2 | 약 1시간 — DDP로 두 장 사용 (권장) |
| Colab T4 (무료) | 16GB | 약 2시간 — 한 장뿐이고 세션 유지가 어렵다 |
| Colab L4 / A100 | 24~40GB | 30분 안팎 |
| 부대 내 GPU 서버 (기획서 3-다: 10~12GB) | 12GB | 가능 (batch 1 + grad_accum 16 권장) |

VRAM이 모자라면 `--model Qwen/Qwen2.5-1.5B-Instruct --batch-size 1 --grad-accum 16`.

## 8. 학습한 모델을 앱에 연결하기

### 8.1 산출물 내려받기

Kaggle 노트북 버전의 **Output** 탭에서 `finetune/out/` 아래를 받는다.

| 받을 것 | 용도 |
|---|---|
| `merged/` | 서빙용 통짜 가중치(fp16). vLLM/Ollama에 올린다 |
| `adapter/` | LoRA 어댑터만(수십 MB). 베이스와 함께 쓰거나 재병합할 때 |
| `comparison.txt` | 튜닝 전/후 지표 비교표 |

병합이 실패했더라도 `adapter/`만 있으면 로컬에서 다시 병합할 수 있다(GPU 불필요, CPU로 돈다).

```bash
python finetune/train_lora.py --model Qwen/Qwen2.5-3B-Instruct \
    --out <adapter의 상위 폴더> --merge ./merged
```

### 8.2 서빙 — Ollama (권장)

Ollama는 safetensors 폴더를 그대로 읽어 GGUF 변환과 양자화까지 한 번에 해 준다.
llama.cpp를 따로 빌드할 필요가 없고, 텐서 단위로 스트리밍 처리하므로 RAM 8GB 정도의
PC에서도 3B 병합본(fp16 약 6GB)을 다룰 수 있다.

```bash
# 1) merged 폴더를 가리키는 Modelfile 작성
#    num_ctx는 우리 프롬프트 길이(FULL 약 1,550토큰)에 여유를 준 값이다.
cat > Modelfile <<'EOF'
FROM ./merged
PARAMETER num_ctx 4096
PARAMETER temperature 0
EOF

# 2) 4bit 양자화하며 등록 (3B Q4_K_M ≈ 2GB — 기획서 3-다 "4bit 2~4GB"에 부합)
ollama create voicecue-qwen2.5-3b --quantize q4_K_M -f Modelfile

# 3) 확인
ollama list
ollama run voicecue-qwen2.5-3b "테스트"
```

Windows에서는 설치 시 서비스가 자동으로 뜨므로 `ollama serve`를 따로 실행하지 않아도
`http://localhost:11434`가 열려 있다. 채팅 템플릿은 병합본에 들어 있는
`tokenizer_config.json`에서 자동으로 읽는다(`train_lora.py`의 merge가 토크나이저를 함께
저장한다).

### 8.2-b 서빙 — vLLM (GPU 서버)

부대 내 GPU 서버처럼 VRAM이 넉넉하면 양자화 없이 그대로 올려도 된다.

```bash
vllm serve ./merged --served-model-name voicecue-qwen2.5-3b --port 8000
```

**어느 쪽이든 서빙 이름을 `voicecue-`로 시작하게 맞추는 것이 중요하다.** 앱이 이 이름을
보고 few-shot을 생략할지 정한다(`llm_engine.is_finetuned`).

### 8.3 앱에서 선택

사이드바에서 공급자를 **"로컬 서버 (Ollama / vLLM)"** 로 바꾸면 `base_url`만 갈아끼워
붙는다. 모델 목록에서 `voicecue-qwen2.5-3b`를 고르면 **"파인튜닝 모델 (few-shot 생략)"**
체크박스가 자동으로 켜진다.

포트가 기본값과 다르면 `.streamlit/secrets.toml`에 `LOCAL_BASE_URL`을 넣는다
(vLLM 기본 `http://localhost:8000/v1`, Ollama 기본 `http://localhost:11434/v1`).

### 8.4 few-shot을 왜 빼는가

학습 데이터를 few-shot 예시 없이 만들었다(`gen_dataset.py` 기본값). 파인튜닝의 목적이
긴 예시 없이도 형식을 지키게 만드는 것이기 때문이다. 그래서 서빙할 때도 빼야 학습 때와
같은 조건이 되고, 입력 토큰이 **FAST 20.1% · FULL 40.4%** 줄어 표출 지연에 직접 기여한다.

튜닝하지 않은 모델에 이 체크박스를 켜면 형식이 무너지므로 꺼 두어야 한다. 반대로 평가
스크립트로 베이스라인과 비교할 때는 **튜닝 전 모델에만 `--few-shot`을 켠다** — 양쪽에
똑같이 켜면 이 절감 효과가 측정되지 않는다.

```bash
python finetune/evaluate.py --backend openai \
    --base-url http://localhost:8000/v1 --model voicecue-qwen2.5-3b
```

### 8.5 로컬 말고 다른 데서 돌리기

이 PC(GTX 970 4GB, RAM 8GB)로도 3B Q4는 돌지만 느리다. 아래 방법은 전부 앱 코드를
건드리지 않는다 — 공급자를 "직접 세운 서버"로 두고 secrets의 세 값만 바꾸면 된다.

```toml
# .streamlit/secrets.toml
LOCAL_BASE_URL = "https://<엔드포인트 주소>/v1"
LOCAL_API_KEY  = "<필요한 경우에만>"
LOCAL_TIMEOUT  = 180          # scale-to-zero 콜드 스타트가 있으면 넉넉히
```

#### 먼저 할 일 — Hugging Face Hub에 올리기

어느 방법을 쓰든 모델이 인터넷에서 접근 가능해야 편하다. **private 저장소**로 올리면
6GB 파일을 매번 들고 다닐 필요가 없다.

```bash
pip install huggingface_hub
huggingface-cli login
huggingface-cli upload <계정>/voicecue-qwen2.5-3b ./merged --private
```

#### 방법 비교

| 방법 | 비용 | 안정성 | 준비 시간 | 적합 |
|---|---|---|---|---|
| Colab/Kaggle + 터널 | 무료 | 세션 끊김 있음 | 15분 | 개발·테스트 |
| HF Inference Endpoints | 분 단위 과금, scale-to-zero | 높음 | 20분 | **시연 당일** |
| RunPod / Vast.ai | 시간당 저렴 | 높음 | 30분 | 장시간 실험 |
| 사내·학교 GPU 서버 | 무료 | 환경에 따름 | — | 접근 가능하면 최선 |

#### A. Colab / Kaggle + 터널 (무료, 테스트용)

이미 쓰던 노트북 환경에 vLLM을 띄우고 터널로 밖에 노출한다. 세션이 끊기면 주소가
바뀌므로 시연 당일에 의존하기엔 위험하다.

```python
!pip install -q vllm
!nohup vllm serve <계정>/voicecue-qwen2.5-3b \
    --served-model-name voicecue-qwen2.5-3b --port 8000 &
# cloudflared로 외부 주소 발급 (계정 불필요)
!wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 -O cloudflared
!chmod +x cloudflared && ./cloudflared tunnel --url http://localhost:8000
```

출력된 `https://...trycloudflare.com` 주소 뒤에 `/v1`을 붙여 `LOCAL_BASE_URL`에 넣는다.

#### B. HF Inference Endpoints (시연 권장)

Hub에 올린 모델 페이지에서 **Deploy → Inference Endpoints**. TGI 컨테이너로 뜨면
`/v1/chat/completions`가 OpenAI 호환으로 열린다. 분 단위 과금이고 **scale-to-zero**로
두면 안 쓸 때는 요금이 붙지 않는다 — 시연 몇 시간만 쓰면 비용이 적다.

- `LOCAL_BASE_URL` = 엔드포인트 URL + `/v1`
- `LOCAL_API_KEY` = HF 액세스 토큰
- `LOCAL_TIMEOUT` = `180` (scale-to-zero는 첫 요청에서 컨테이너가 깨어나느라 오래 걸린다)

#### C. RunPod / Vast.ai

GPU를 시간당 빌려 vLLM을 직접 띄운다. 8.2-b의 명령을 그대로 쓰고, 포트를 공개한 뒤
그 주소를 `LOCAL_BASE_URL`에 넣는다. 결제수단 등록이 필요하다.

#### 기획서 서술과의 관계

클라우드에서 돌린다고 해서 "폐쇄망 온프레미스" 주장이 약해지지는 않는다. 핵심은
**모델이 3B/4bit라 부대 서버 한 대(기획서 3-다: 10~12GB)에 들어간다**는 점이고,
클라우드는 시연 편의를 위한 것일 뿐 같은 가중치를 부대 서버에 올리면 그대로 돈다.
심사에서 물어보면 그렇게 답하면 되고, 8.2절의 Ollama 구성이 그 증거다.

### 8.6 배포에서 실제로 걸린 것들 (실측)

허브에 올린 모델을 HF Inference Endpoints(TGI)로 띄워 앱에 붙이면서 실제로 막혔던
지점과 해결책이다. 어느 서빙 경로에서도 똑같이 나올 수 있는 문제라 남겨 둔다.

| 증상 | 원인 | 해결 |
|---|---|---|
| `404 Not Found` (`/v1/chat/completions`) | 엔드포인트가 TGI가 아니라 Default 컨테이너로 생성됨. 컨테이너 종류는 생성 후 변경 불가 | 엔드포인트를 지우고 TGI로 다시 생성. `GET /info`가 200이면 TGI, 404면 Default |
| `503 Service Unavailable` | Scale-to-Zero로 잠들어 있음 | 첫 요청 후 4~5분 대기(콜드 스타트). `LOCAL_TIMEOUT`을 180초 이상으로 |
| `403 ... missing permissions: inference.endpoints.infer.write` | 토큰에 추론 권한 없음 | fine-grained 토큰에 Inference 권한 부여 |
| `422 response_format: missing field 'value'` | TGI는 OpenAI 규격과 달리 `response_format`에 JSON 스키마(`value`)를 요구 | 앱이 알아서 처리한다. `llm_engine.call_llm`이 400/422를 받으면 `response_format`을 빼고 재시도하고, 그 모델을 세션 동안 기억해 다시 보내지 않는다 |
| `422 Template error: template not found` | transformers 4.56부터 채팅 템플릿을 `tokenizer_config.json`이 아니라 별도 `chat_template.jinja`로 저장하는데, TGI/vLLM/Ollama는 `tokenizer_config.json`의 `chat_template` 키만 읽는다 | `python finetune/fix_chat_template.py <repo_id>` (쓰기 권한 토큰 필요) 실행 후 엔드포인트 Pause → Resume. 앞으로 학습하는 모델은 `train_lora.py`의 `embed_chat_template()`이 병합 직후에 자동으로 심는다 |

마지막 항목은 학습은 멀쩡한데 배포만 죽는 형태라 원인을 찾기 어렵다. 모델 자체가
정상인지 먼저 가르려면 채팅 템플릿을 안 쓰는 경로로 찔러 보면 된다 —
`/v1/completions`에 ChatML(`<|im_start|>system ... <|im_end|>`)을 손으로 조립해
보내서 JSON이 나오면 모델은 정상이고 서버 템플릿만 문제다.

## 9. 알려진 한계

- **합성 데이터다.** 기획서 4-다①이 말한 "실제 회의록·작전상황일지"가 아니라
  `scenario_bank.py`의 템플릿에서 생성한 가상 시나리오다. 문장 다양성이 실제 회의보다
  좁으므로, 이 데이터만으로 학습한 모델은 템플릿 밖 표현에 약할 수 있다. 실데이터가
  확보되면 `scenario_bank.py`를 교체하고 `gen_dataset.py`는 그대로 쓰면 된다.
- **지표 ③(숙련도별 품질 편차)은 자동 측정 대상이 아니다.** 사람 운용자 두 집단의
  비교 실험이 필요하다. 다만 화면 배치가 플레이북 기반 결정론이라 모델 쪽 편차는
  구조적으로 0이며, 이것이 기획서가 말한 "숙련도와 무관한 일관성"의 근거다.
- **아직 기획서 목표치(90%)에 못 미친다.** 4절 비교표 기준 상황유형 정확도 75%,
  키워드 정확도 84.9%다. 1.5B/1에폭 결과이므로 3B 또는 에폭 증가로 개선 여지가 있고,
  그 실측은 아직 하지 않았다.
- 운영 중 누적되는 사용자 보정(`user_corrections`)을 재학습에 편입하는 경로
  (기획서 4-⑤ "유지보수 시")는 `CONSTRAINT_TURNS`로 형태만 모사했다. 실제 운영 로그를
  학습 데이터로 환류하는 파이프라인은 아직 없다.

"""VOICE-CUE 파인튜닝 평가 하네스 (기획서 3-라 4개 지표 / 4-다③).

기획서가 요구한 평가는 두 가지다.
    4-다③ "핵심 키워드 포함 여부(Keyword Accuracy)와 문장 생성 품질(BLEU/ROUGE)"
    3-라   4개 품질 지표 — ①표출 지연 ②상위배치 정확도 ③숙련도 편차 ④일지 정확도·누락률

지표별 구현 위치:
    ① 필요정보 표출 지연시간   → fast_latency_p50 / p95 (FAST 경로 실측, 목표 5초 이내)
    ② 핵심정보 상위배치 정확도 → cop_exact / cop_cell_match
                                 예측한 상황 유형을 playbook.build_layout()에 넣어 나온
                                 레이아웃을 전문가 정답 레이아웃(cop_reference)과 비교한다.
                                 cop_cell_match는 2행 6열 열두 칸을 펼쳐 같은 자리에 같은
                                 화면이 떠 있는 비율이라, 큰 자리를 틀릴수록 크게 깎인다.
                                 배치는 플레이북이 결정하므로 유형만 맞히면 100%가 되며,
                                 이것이 "일치율 90% 이상"을 구조적으로 보장하는 설계다.
    ③ 숙련도별 품질 편차       → 사람 운용자 비교 실험이 필요해 이 스크립트 범위 밖.
                                 다만 ②가 결정론적이라 모델 쪽 편차는 0이며, 그 근거로
                                 cop_exact의 시드별 표준편차를 함께 출력한다.
    ④ 작전일지 정확도·누락률   → keyword_accuracy / omission_rate
                                 (+ log_kind_accuracy, event_link_accuracy, rouge_l, bleu4)

백엔드:
    gold     정답을 그대로 예측으로 돌려준다. 하네스 자기검증용 — 전 지표 1.0이어야 한다.
    perturb  정답을 --noise 비율만큼 일부러 망가뜨린다. 지표가 실제로 하락하는지 확인용.
    openai   OpenAI 호환 Chat Completions. 파인튜닝 모델을 vLLM/Ollama로 서빙하거나,
             베이스라인 측정을 위해 OpenRouter에 붙일 때 쓴다.
    hf       transformers + PEFT 어댑터를 로컬에서 직접 로드한다.

사용:
    python finetune/evaluate.py --backend gold
    python finetune/evaluate.py --backend perturb --noise 0.3
    python finetune/evaluate.py --backend openai --base-url http://localhost:8000/v1 \
        --model voicecue-qwen2.5-3b --few-shot        # 베이스라인은 few-shot 켜고 비교
    python finetune/evaluate.py --backend hf --model Qwen/Qwen2.5-3B-Instruct \
        --adapter finetune/out/adapter
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import playbook as pb  # noqa: E402
from modules import prompts  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"

# llm_engine._extract_json과 같은 규칙. 저 모듈은 streamlit을 import하므로 학습·평가
# 환경에서 그대로 쓸 수 없어, 파싱 규칙만 여기 옮겨 둔다. 규칙이 바뀌면 양쪽을 함께 고칠 것.
_CODE_FENCE_RE = re.compile(r"^```(?:json)?|```$", re.MULTILINE)


def extract_json(raw_text: str, required_keys: tuple[str, ...]) -> dict:
    text = _CODE_FENCE_RE.sub("", raw_text.strip()).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("응답에서 JSON 객체를 찾을 수 없습니다.")
    parsed = json.loads(text[start:end + 1])
    missing = [k for k in required_keys if k not in parsed]
    if missing:
        raise ValueError(f"필수 키 누락: {missing}")
    return parsed


# ---------------------------------------------------------------------
# 문장 유사도 — 외부 의존성 없이 구현한다 (폐쇄망 학습 서버를 전제로)
# ---------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
# 일지 문장에서 정보량이 없는 상투어. 키워드 정확도가 이런 토큰으로 부풀지 않게 뺀다.
_STOPWORDS = {
    "합니다", "습니다", "입니다", "했습니다", "됩니다", "중입니다", "있습니다",
    "확인", "중", "및", "등", "그리고", "현재", "예정", "조치", "실시", "완료",
}


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text or "")


def keywords(text: str) -> list[str]:
    return [t for t in tokenize(text) if len(t) >= 2 and t not in _STOPWORDS]


def _lcs_length(a: list[str], b: list[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b):
            cur.append(prev[j] + 1 if x == y else max(cur[j], prev[j + 1]))
        prev = cur
    return prev[-1]


def rouge_l(reference: str, hypothesis: str) -> float:
    """어절 단위 LCS 기반 F1. 둘 다 비어 있으면 1.0(정답도 예측도 '기록 없음')."""
    ref, hyp = tokenize(reference), tokenize(hypothesis)
    if not ref and not hyp:
        return 1.0
    if not ref or not hyp:
        return 0.0
    lcs = _lcs_length(ref, hyp)
    if lcs == 0:
        return 0.0
    precision, recall = lcs / len(hyp), lcs / len(ref)
    return 2 * precision * recall / (precision + recall)


def bleu4(reference: str, hypothesis: str) -> float:
    """어절 n-gram BLEU-4 (smoothing +1, brevity penalty 포함)."""
    ref, hyp = tokenize(reference), tokenize(hypothesis)
    if not ref and not hyp:
        return 1.0
    if not ref or not hyp:
        return 0.0
    log_sum = 0.0
    for n in range(1, 5):
        ref_ngrams = Counter(tuple(ref[i:i + n]) for i in range(len(ref) - n + 1))
        hyp_ngrams = Counter(tuple(hyp[i:i + n]) for i in range(len(hyp) - n + 1))
        overlap = sum((ref_ngrams & hyp_ngrams).values())
        total = max(sum(hyp_ngrams.values()), 0)
        # 짧은 문장이라 4-gram이 아예 없는 경우가 흔하다. +1 스무딩으로 0 붕괴를 막는다.
        log_sum += _log((overlap + 1) / (total + 1))
    brevity = 1.0 if len(hyp) > len(ref) else _exp(1 - len(ref) / max(len(hyp), 1))
    return brevity * _exp(log_sum / 4)


def _log(x: float) -> float:
    import math
    return math.log(max(x, 1e-12))


def _exp(x: float) -> float:
    import math
    return math.exp(x)


def keyword_accuracy(reference: str, hypothesis: str) -> float:
    """기획서 3-라④ "정답 일지 대비 키워드 일치율"."""
    ref_kw = keywords(reference)
    if not ref_kw:
        return 1.0 if not keywords(hypothesis) else 0.0
    hyp_text = hypothesis or ""
    return sum(1 for k in ref_kw if k in hyp_text) / len(ref_kw)


def _paint(panels: list[dict], id_key: str) -> dict[tuple[int, int], str]:
    """레이아웃을 (행, 열) -> source_id 격자로 펼친다."""
    cells: dict[tuple[int, int], str] = {}
    for panel in panels:
        row, col, rspan, cspan = panel["grid"]
        for r in range(row, row + rspan):
            for c in range(col, col + cspan):
                cells[(r, c)] = panel[id_key]
    return cells


def _cell_agreement(gold_panels: list[dict], pred_layout: list[dict]) -> float:
    """기획서 3-라② "핵심정보 상위배치 정확도" — 벽면 칸 단위 일치율.

    화면 이름의 집합만 비교하면 큰 자리에 뜬 핵심 화면과 구석의 한 칸짜리 보조
    화면이 똑같이 1점이 된다. 실제로 지휘관이 보는 것은 면적이므로, 2행 6열
    열두 칸을 각각 펼쳐 같은 자리에 같은 화면이 떠 있는 비율로 센다. 1순위
    대형 화면을 틀리면 한 번에 4칸을 잃는다.

    정답 쪽은 데이터셋에 저장된 panels(우선순위·자리·크기 포함)를, 예측 쪽은
    예측한 상황 유형으로 방금 만든 레이아웃을 쓴다.
    """
    gold_cells = _paint(gold_panels, "name")
    pred_cells = _paint(
        [{"grid": item["grid"], "name": item["name"]} for item in pred_layout], "name"
    )
    if not gold_cells:
        return 1.0 if not pred_cells else 0.0
    hit = sum(1 for cell, name in gold_cells.items() if pred_cells.get(cell) == name)
    return hit / len(gold_cells)


_EVENT_ID_RE = re.compile(r"^(?:사태|사건|상황|이벤트|event)\s*[-_]?\s*(\d+)$", re.IGNORECASE)


def normalize_event_id(raw: object) -> str:
    """context_memory._normalize_event_id와 같은 규칙 — 런타임이 흡수해 주는 표기
    흔들림("사건2")까지 오답으로 세면 실제 시스템 정확도를 과소평가하게 된다."""
    text = str(raw or "").strip()
    if not text:
        return ""
    m = _EVENT_ID_RE.match(text)
    return f"사태{int(m.group(1))}" if m else text


# ---------------------------------------------------------------------
# 백엔드
# ---------------------------------------------------------------------

class GoldBackend:
    """정답을 그대로 돌려준다. 지표 계산식 자체가 맞는지 검증하는 용도."""

    name = "gold"

    def __init__(self, **_: object) -> None:
        pass

    def generate(self, turn: dict, route: str) -> tuple[str, float]:
        target = turn["fast_target"] if route == "fast" else turn["full_target"]
        return json.dumps(target, ensure_ascii=False), 0.0


class PerturbBackend(GoldBackend):
    """정답을 일부러 망가뜨린다. 하네스가 오류를 실제로 잡아내는지 확인하는 용도."""

    name = "perturb"

    def __init__(self, noise: float = 0.3, seed: int = 0, **_: object) -> None:
        self.noise = noise
        self.rng = random.Random(seed)
        self.situations = pb.situation_names()

    def generate(self, turn: dict, route: str) -> tuple[str, float]:
        if route == "fast":
            data = json.loads(json.dumps(turn["fast_target"], ensure_ascii=False))
            if self.rng.random() < self.noise:
                data["situation"]["type"] = self.rng.choice(self.situations)
            return json.dumps(data, ensure_ascii=False), 0.0

        data = json.loads(json.dumps(turn["full_target"], ensure_ascii=False))
        if self.rng.random() < self.noise:
            entry = data["operation_log_entry"]
            # 실제로 자주 나는 오류 두 가지: 후속을 새 사태로 만들기, 내용 누락.
            if entry["kind"] == "조치":
                entry["kind"] = "상황"
                entry["event_id"] = "사태99"
            else:
                entry["kind"] = "무시"
                entry["event_id"], entry["content"] = "", ""
        return json.dumps(data, ensure_ascii=False), 0.0


class OpenAIBackend:
    """OpenAI 호환 엔드포인트. vLLM/Ollama로 서빙한 파인튜닝 모델도 여기로 붙는다."""

    name = "openai"

    def __init__(self, base_url: str, model: str, api_key: str | None,
                 few_shot: bool = False, max_tokens: int = 700, **_: object) -> None:
        import openai
        self.client = openai.OpenAI(base_url=base_url, api_key=api_key or "not-needed")
        self.model = model
        self.few_shot = few_shot
        self.max_tokens = max_tokens

    def generate(self, turn: dict, route: str) -> tuple[str, float]:
        if route == "fast":
            system, user = prompts.FAST_SYSTEM_PROMPT, turn["fast_user"]
            shots = prompts.FAST_FEW_SHOT_MESSAGES if self.few_shot else []
        else:
            system, user = prompts.FULL_SYSTEM_PROMPT, turn["full_user"]
            shots = prompts.FULL_FEW_SHOT_MESSAGES if self.few_shot else []

        messages = [{"role": "system", "content": system}, *shots,
                    {"role": "user", "content": user}]
        start = time.monotonic()
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, temperature=0.0,
            max_tokens=self.max_tokens, response_format={"type": "json_object"},
        )
        return response.choices[0].message.content or "", time.monotonic() - start


class HFBackend:
    """transformers + PEFT 어댑터 직접 로드. 서빙 없이 어댑터만 빠르게 확인할 때."""

    name = "hf"

    def __init__(self, model: str, adapter: str | None = None,
                 few_shot: bool = False, max_tokens: int = 700, **_: object) -> None:
        import torch
        from transformers import AutoModelForCausalLM, AutoTokenizer

        # 학습 쪽(train_lora.py)과 같은 규칙. torch.cuda.is_bf16_supported()는
        # including_emulation 기본값이 True라 T4(SM 75)에서도 True를 돌려주고,
        # 그러면 가속 경로가 없는 에뮬레이션 bf16으로 떨어져 추론이 크게 느려진다.
        # 연산 능력을 직접 보고 판단한다.
        capability = torch.cuda.get_device_capability() if torch.cuda.is_available() else (0, 0)
        use_bf16 = capability[0] >= 8
        # dtype 인자 이름이 transformers 4.56에서 torch_dtype -> dtype으로 바뀌었다.
        # 옛 이름은 경고만 내고 반영되지 않을 수 있어 버전에 맞는 이름으로 넘긴다
        # (자세한 배경은 train_lora.py 주석 참고).
        import transformers
        try:
            _major, _minor = (int(x) for x in transformers.__version__.split(".")[:2])
            dtype_kw = "dtype" if (_major, _minor) >= (4, 56) else "torch_dtype"
        except ValueError:
            dtype_kw = "dtype"
        compute_dtype = torch.bfloat16 if use_bf16 else torch.float16
        load_kwargs: dict = {
            dtype_kw: compute_dtype,
            # 여러 장이어도 쪼개지 않는다 — 이유는 train_lora.py 주석 참고.
            "device_map": {"": 0} if torch.cuda.is_available() else "auto",
        }
        # GPU가 있으면 학습·배포와 같은 4bit로 올린다. 두 가지 이유다.
        #
        # 하나, 기획서 3-다가 요구하는 구성이 4bit(2~4GB)이므로 fp16으로 재보면 실제
        # 배포될 모델이 아닌 것을 재게 된다.
        #
        # 둘, fp16으로 올리면 대상 모듈이 평범한 nn.Linear라서 PEFT가 어댑터를 끼울 때
        # torchao 디스패처를 먼저 시도하는데, PEFT의 is_torchao_available()은 torchao가
        # 낮은 버전으로 깔려 있으면 False를 돌려주는 대신 ImportError를 던진다.
        # Kaggle 이미지에 torchao 0.10.0이 미리 깔려 있어 여기서 죽었다(PEFT 요구는
        # 0.16 초과). 4bit로 올리면 bnb 디스패처가 먼저 매칭되어 그 경로를 타지 않는다.
        if torch.cuda.is_available():
            from transformers import BitsAndBytesConfig
            load_kwargs["quantization_config"] = BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=compute_dtype,
                bnb_4bit_use_double_quant=True,
            )

        self.tokenizer = AutoTokenizer.from_pretrained(model)
        self.model = AutoModelForCausalLM.from_pretrained(model, **load_kwargs)
        if adapter:
            # GPU가 없어 4bit로 못 올린 경우(CPU 평가)에는 대상이 평범한 nn.Linear라
            # PEFT가 torchao 디스패처를 먼저 시도하다 버전 검사에서 죽을 수 있다.
            # 미리 막아 둔다 — 자세한 내용은 finetune/compat.py 참고.
            from finetune import compat
            compat.patch_peft_torchao_check()
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, adapter)
        self.model.eval()
        self.few_shot = few_shot
        self.max_tokens = max_tokens

    def generate(self, turn: dict, route: str) -> tuple[str, float]:
        if route == "fast":
            system, user = prompts.FAST_SYSTEM_PROMPT, turn["fast_user"]
            shots = prompts.FAST_FEW_SHOT_MESSAGES if self.few_shot else []
        else:
            system, user = prompts.FULL_SYSTEM_PROMPT, turn["full_user"]
            shots = prompts.FULL_FEW_SHOT_MESSAGES if self.few_shot else []

        messages = [{"role": "system", "content": system}, *shots,
                    {"role": "user", "content": user}]
        text = self.tokenizer.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        inputs = self.tokenizer(text, return_tensors="pt").to(self.model.device)
        start = time.monotonic()
        import torch
        with torch.no_grad():
            out = self.model.generate(
                **inputs, max_new_tokens=self.max_tokens, do_sample=False,
                pad_token_id=self.tokenizer.eos_token_id,
            )
        generated = out[0][inputs["input_ids"].shape[1]:]
        return self.tokenizer.decode(generated, skip_special_tokens=True), time.monotonic() - start


# ---------------------------------------------------------------------
# 채점
# ---------------------------------------------------------------------

def evaluate(turns: list[dict], backend: object, limit: int | None = None) -> dict:
    if limit:
        turns = turns[:limit]

    fast_json_ok = fast_correct = 0
    cop_exact = cop_overlap_sum = 0.0
    fast_latencies: list[float] = []

    full_json_ok = kind_correct = link_total = link_correct = 0
    kw_sum = rouge_sum = bleu_sum = mem_rouge_sum = 0.0
    omissions = board_rank1_correct = 0
    full_latencies: list[float] = []
    confusion: Counter = Counter()

    for turn in turns:
        # ---------- FAST (표출 경로) ----------
        try:
            raw, latency = backend.generate(turn, "fast")
            data = extract_json(raw, ("situation",))
            predicted = str((data.get("situation") or {}).get("type", "")).strip()
            fast_json_ok += 1
            fast_latencies.append(latency)
        except Exception:
            predicted = ""

        gold_type = turn["fast_target"]["situation"]["type"]
        # 런타임(context_memory.apply_fast_result)과 같은 관대한 매칭을 적용한다.
        matched = pb.find_situation(predicted)
        resolved = matched["name"] if matched else "기타 상황"
        if resolved == gold_type:
            fast_correct += 1
        else:
            confusion[f"{gold_type} → {resolved}"] += 1

        # 지표② — 예측 유형으로 실제 레이아웃을 만들어 전문가 정답과 비교한다.
        layout, _ = pb.build_layout(resolved, turn["utterance"])
        pred_ids = [item["source_id"] for item in layout]
        gold_ids = turn["cop_reference"]["source_ids"]
        if pred_ids == gold_ids:
            cop_exact += 1
        cop_overlap_sum += _cell_agreement(turn["cop_reference"]["panels"], layout)

        # ---------- FULL (기록 경로) ----------
        try:
            raw, latency = backend.generate(turn, "full")
            data = extract_json(raw, ("context_memory", "situation_board", "operation_log_entry"))
            full_json_ok += 1
            full_latencies.append(latency)
        except Exception:
            data = {"context_memory": "", "situation_board": [], "operation_log_entry": {}}

        gold_entry = turn["full_target"]["operation_log_entry"]
        pred_entry = data.get("operation_log_entry") or {}
        pred_kind = str(pred_entry.get("kind", "")).strip()
        pred_content = str(pred_entry.get("content", "") or "")

        if pred_kind == gold_entry["kind"]:
            kind_correct += 1
        if gold_entry["kind"] == "조치":
            link_total += 1
            if normalize_event_id(pred_entry.get("event_id")) == normalize_event_id(gold_entry["event_id"]):
                link_correct += 1

        kw_sum += keyword_accuracy(gold_entry["content"], pred_content)
        rouge_sum += rouge_l(gold_entry["content"], pred_content)
        bleu_sum += bleu4(gold_entry["content"], pred_content)
        # 기획서 3-라④ 누락률 — 기록해야 할 발언인데 내용이 비었거나 무시로 버려진 경우.
        if gold_entry["kind"] in ("상황", "조치") and (pred_kind == "무시" or not pred_content.strip()):
            omissions += 1

        mem_rouge_sum += rouge_l(turn["full_target"]["context_memory"],
                                 str(data.get("context_memory", "") or ""))

        gold_board = turn["full_target"]["situation_board"]
        pred_board = sorted(data.get("situation_board") or [], key=lambda b: b.get("rank", 99))
        if gold_board and pred_board:
            # 1순위 사태는 표현이 조금 달라도 같은 사태를 가리키면 맞은 것으로 본다.
            if rouge_l(gold_board[0].get("event", ""), str(pred_board[0].get("event", ""))) >= 0.5:
                board_rank1_correct += 1
        elif not gold_board and not pred_board:
            board_rank1_correct += 1

    n = len(turns)
    gold_action_turns = sum(
        1 for t in turns if t["full_target"]["operation_log_entry"]["kind"] in ("상황", "조치")
    )

    def pct(x: float, total: int = n) -> float:
        return round(100 * x / total, 2) if total else 0.0

    result = {
        "backend": getattr(backend, "name", "?"),
        "turns": n,
        "지표①_표출지연": {
            "fast_p50_sec": round(statistics.median(fast_latencies), 3) if fast_latencies else 0.0,
            "fast_p95_sec": round(sorted(fast_latencies)[int(len(fast_latencies) * 0.95) - 1], 3)
            if len(fast_latencies) >= 20 else (round(max(fast_latencies), 3) if fast_latencies else 0.0),
            "full_p50_sec": round(statistics.median(full_latencies), 3) if full_latencies else 0.0,
            "목표": "5초 이내",
        },
        "지표②_상위배치정확도": {
            "cop_exact_pct": pct(cop_exact),
            "cop_cell_match_pct": round(100 * cop_overlap_sum / n, 2) if n else 0.0,
            "situation_accuracy_pct": pct(fast_correct),
            "목표": "90% 이상",
        },
        "지표④_일지정확도": {
            "keyword_accuracy_pct": round(100 * kw_sum / n, 2) if n else 0.0,
            "omission_rate_pct": pct(omissions, gold_action_turns),
            "log_kind_accuracy_pct": pct(kind_correct),
            "event_link_accuracy_pct": pct(link_correct, link_total),
            "rouge_l_pct": round(100 * rouge_sum / n, 2) if n else 0.0,
            "bleu4_pct": round(100 * bleu_sum / n, 2) if n else 0.0,
            "목표": "정확도 90% / 누락률 5% 미만",
        },
        "부가": {
            "fast_json_valid_pct": pct(fast_json_ok),
            "full_json_valid_pct": pct(full_json_ok),
            "context_memory_rouge_l_pct": round(100 * mem_rouge_sum / n, 2) if n else 0.0,
            "board_rank1_accuracy_pct": pct(board_rank1_correct),
        },
        "오분류_상위": dict(confusion.most_common(10)),
    }
    return result


def load_turns(split: str) -> list[dict]:
    path = DATA_DIR / f"turns_{split}.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} 가 없습니다. 먼저 python finetune/gen_dataset.py 를 실행하세요.")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", choices=["gold", "perturb", "openai", "hf"], default="gold")
    ap.add_argument("--split", default="test")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--noise", type=float, default=0.3, help="perturb 백엔드 오류 주입 비율")
    ap.add_argument("--base-url", default="http://localhost:8000/v1")
    ap.add_argument("--model", default="Qwen/Qwen2.5-3B-Instruct")
    ap.add_argument("--adapter", default=None)
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--few-shot", action="store_true",
                    help="프롬프트에 few-shot 예시를 넣는다. 튜닝 전 베이스라인 측정용.")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    backends = {"gold": GoldBackend, "perturb": PerturbBackend,
                "openai": OpenAIBackend, "hf": HFBackend}
    backend = backends[args.backend](
        noise=args.noise, base_url=args.base_url, model=args.model,
        adapter=args.adapter, api_key=args.api_key or os.environ.get("OPENAI_API_KEY"),
        few_shot=args.few_shot,
    )

    result = evaluate(load_turns(args.split), backend, args.limit)
    result["split"] = args.split
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if args.out:
        args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n저장: {args.out}")

    if args.backend == "gold":
        # 자기검증 — 정답을 그대로 넣었는데 100%가 아니면 지표 계산식이 틀린 것이다.
        checks = {
            "상황유형 정확도": result["지표②_상위배치정확도"]["situation_accuracy_pct"],
            "COP 완전일치": result["지표②_상위배치정확도"]["cop_exact_pct"],
            "일지 kind 정확도": result["지표④_일지정확도"]["log_kind_accuracy_pct"],
            "사태 연결 정확도": result["지표④_일지정확도"]["event_link_accuracy_pct"],
            "키워드 정확도": result["지표④_일지정확도"]["keyword_accuracy_pct"],
            "ROUGE-L": result["지표④_일지정확도"]["rouge_l_pct"],
        }
        failed = {k: v for k, v in checks.items() if v < 100.0}
        print("\n[하네스 자기검증] " + ("통과 — 전 지표 100%"
              if not failed else f"실패 — {failed}"))
        if result["지표④_일지정확도"]["omission_rate_pct"] != 0.0:
            print("[하네스 자기검증] 실패 — gold인데 누락률이 0이 아님")


if __name__ == "__main__":
    main()

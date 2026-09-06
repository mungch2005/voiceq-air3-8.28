"""VOICE-CUE sLLM QLoRA 파인튜닝 (기획서 4-다② / 3-다).

기획서가 지정한 조건:
    4-다② "폐쇄망 구동이 가능한 오픈소스 한국어 특화 sLLM(Qwen 2.5 또는 Llama-3)을
           선정하고, LoRA 방식으로 전체가 아닌 필요한 부분만 효율적으로 학습"
    3-다   "판단·구성 모델 — 한국어 특화 sLLM, 4bit 양자화 … 2~4GB"

기본 모델을 Qwen2.5-3B-Instruct로 잡은 이유:
    4bit(nf4) 가중치가 약 2GB로 기획서의 "2~4GB" 구간 한가운데에 들어온다.
    7B는 4bit로도 4.5GB 안팎이라 상한을 넘고, 1.5B는 여유는 있지만 한국어 지시
    이행이 눈에 띄게 불안정하다. VRAM이 부족하면 --model 로 1.5B를 지정하면 된다.

학습 손실은 assistant 응답 구간에만 건다(completion-only). 시스템 프롬프트가 길고
매 샘플 동일하므로, 거기에도 손실을 걸면 모델이 프롬프트 암기에 용량을 쓰고 정작
JSON 형식 준수는 덜 배운다.

few-shot 예시는 학습 데이터에 넣지 않는다(gen_dataset.py 기본값). 파인튜닝의 목적이
few-shot 없이도 형식을 지키게 만들어 입력 토큰과 지연시간을 줄이는 것이기 때문이다 —
기획서 3-다 "명령 생성 2초 이내"에 직접 기여한다.

로컬 GTX 970(4GB, Compute Capability 5.2)에서는 학습할 수 없다. bitsandbytes의 4bit
커널이 Ampere/Turing 이상을 요구하고 VRAM도 모자란다. Colab T4(16GB) 이상 또는
부대 내 GPU 서버에서 실행하는 것을 전제로 한다. --dry-run은 GPU·torch 없이도
데이터셋과 토큰 길이를 점검한다.

사용:
    python finetune/train_lora.py --dry-run           # 데이터·길이 점검만 (torch 불필요)
    python finetune/train_lora.py                     # 실제 학습
    python finetune/train_lora.py --merge finetune/out/merged   # 어댑터 병합 후 저장
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from pathlib import Path

# finetune 패키지(compat 등)를 어디서 실행하든 임포트할 수 있게 저장소 루트를 넣는다.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_MODEL = "Qwen/Qwen2.5-3B-Instruct"


def load_sft(split: str) -> list[dict]:
    # DATA_DIR은 --data로 바뀔 수 있다(main에서 재할당). 기본 데이터와 레이아웃 변형
    # 데이터(gen_dataset.py --layout-target)를 같은 스크립트로 학습하기 위한 것이다.
    path = DATA_DIR / f"sft_{split}.jsonl"
    if not path.exists():
        raise SystemExit(f"{path} 가 없습니다. 먼저 python finetune/gen_dataset.py 를 실행하세요.")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _dtype_kwarg() -> str:
    """from_pretrained에 dtype을 넘길 때 쓸 인자 이름.

    transformers 4.56에서 torch_dtype -> dtype으로 바뀌었다. 옛 이름을 쓰면
    "torch_dtype is deprecated!" 경고만 뜨고 요청한 dtype이 반영되지 않을 수 있는데,
    Qwen2.5의 config.json은 torch_dtype이 bfloat16이라 반영이 안 되면 모델이 통째로
    bf16으로 올라온다 — T4에서는 에뮬레이션이라 느리고, 어댑터까지 bf16이 되면
    fp16 GradScaler가 기울기를 못 다뤄 학습이 죽는다.
    """
    import transformers
    try:
        major, minor = (int(x) for x in transformers.__version__.split(".")[:2])
    except ValueError:
        return "dtype"
    return "dtype" if (major, minor) >= (4, 56) else "torch_dtype"


def _estimate_tokens(text: str) -> int:
    """토크나이저 없이 쓰는 근사치. 한글은 문자당 대략 0.7토큰, 그 외는 0.3토큰으로 본다.

    Qwen2.5 BPE 기준 실측에 맞춘 어림수이며, --dry-run에서 max_seq_len을 정할 때
    자리를 잡기 위한 용도다. 정확한 값이 필요하면 transformers를 설치하면 dry-run이
    자동으로 실제 토크나이저를 쓴다.
    """
    hangul = sum(1 for ch in text if "가" <= ch <= "힣")
    return int(hangul * 0.7 + (len(text) - hangul) * 0.3)


def dry_run(args: argparse.Namespace) -> None:
    """torch 없이 데이터셋을 점검한다. 학습 환경에 올리기 전 로컬에서 돌리는 관문."""
    try:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(args.model)

        def count(messages: list[dict]) -> int:
            # tokenize=True의 반환 타입이 transformers 버전마다 다르다(4.x는 id 리스트,
            # 5.x는 BatchEncoding). 문자열로 렌더한 뒤 따로 인코딩하면 버전과 무관하다.
            text = tokenizer.apply_chat_template(messages, tokenize=False)
            return len(tokenizer(text, add_special_tokens=False)["input_ids"])
        source = f"실제 토크나이저 ({args.model})"
    except Exception as exc:  # transformers 미설치 또는 오프라인
        tokenizer = None

        def count(messages: list[dict]) -> int:
            return sum(_estimate_tokens(m["content"]) for m in messages) + 4 * len(messages)
        source = f"근사치 추정 (토크나이저 사용 불가: {type(exc).__name__})"

    print(f"[dry-run] 토큰 길이 계산: {source}\n")

    total = 0
    for split in ("train", "valid", "test"):
        rows = load_sft(split)
        total += len(rows)
        by_route: dict[str, list[int]] = {"fast": [], "full": []}
        for row in rows:
            # 마지막 메시지가 assistant 응답 — 손실이 걸리는 구간이다.
            assert row["messages"][-1]["role"] == "assistant", "마지막 메시지가 assistant가 아님"
            assert row["messages"][0]["role"] == "system", "첫 메시지가 system이 아님"
            json.loads(row["messages"][-1]["content"])  # 타깃이 유효 JSON인지
            by_route[row["route"]].append(count(row["messages"]))

        print(f"[{split}] {len(rows)}건")
        for route, lengths in by_route.items():
            if not lengths:
                continue
            lengths.sort()
            print(f"   {route:5s} n={len(lengths):5d}  "
                  f"median={statistics.median(lengths):6.0f}  "
                  f"p95={lengths[int(len(lengths) * 0.95) - 1]:6.0f}  "
                  f"max={lengths[-1]:6.0f}")

    print(f"\n총 SFT 샘플 {total}건")
    all_lengths = [count(r["messages"]) for split in ("train", "valid")
                   for r in load_sft(split)]
    p99 = sorted(all_lengths)[int(len(all_lengths) * 0.99) - 1]
    print(f"train+valid p99 길이 {p99} → --max-seq-len {args.max_seq_len} "
          f"{'충분' if p99 <= args.max_seq_len else '부족: 잘림 발생'}")
    print("[dry-run] 통과 — 스키마·역할 순서·타깃 JSON 유효성 이상 없음")


def train(args: argparse.Namespace) -> None:
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig
    from trl import SFTConfig, SFTTrainer

    tokenizer = AutoTokenizer.from_pretrained(args.model)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # bf16 텐서코어는 Ampere(SM 80)부터다. torch.cuda.is_bf16_supported()를 쓰면 안 된다 —
    # 이 함수는 including_emulation이 기본 True라 T4(Turing, SM 75)에서도 True를 돌려준다.
    # 그러면 가속 경로가 없는 에뮬레이션 bf16으로 떨어져 학습이 몇 배 느려진다(Kaggle
    # T4 실측: 135초/스텝). 연산 능력을 직접 보고 8.0 미만이면 fp16을 쓴다.
    capability = torch.cuda.get_device_capability() if torch.cuda.is_available() else (0, 0)
    use_bf16 = capability[0] >= 8
    compute_dtype = torch.bfloat16 if use_bf16 else torch.float16
    gpu_name = torch.cuda.get_device_name(0) if torch.cuda.is_available() else "CPU"
    n_gpu = torch.cuda.device_count() if torch.cuda.is_available() else 0
    print(f"[train] {gpu_name} (SM {capability[0]}.{capability[1]}, {n_gpu}개) · "
          f"연산 dtype {'bfloat16' if use_bf16 else 'float16'}")

    quant_config = BitsAndBytesConfig(
        load_in_4bit=True,
        bnb_4bit_quant_type="nf4",
        bnb_4bit_compute_dtype=compute_dtype,
        bnb_4bit_use_double_quant=True,   # 기획서의 4bit 2~4GB 목표를 맞추기 위한 이중 양자화
    )
    # 모델은 항상 GPU 한 장에 통째로 올린다.
    #
    # device_map="auto"로 두면 GPU가 둘일 때 모델을 층 단위로 쪼개 나눠 싣는데(모델
    # 병렬), 1.5B~3B는 한 장(16GB)에 4bit로 충분히 들어가므로 쪼갤 이유가 없다.
    # 쪼개면 층마다 GPU 간 전송이 생기고 두 GPU가 번갈아 놀아서 오히려 느려진다.
    #
    # GPU 두 장을 제대로 쓰는 방법은 데이터 병렬(DDP)이다. 각 프로세스가 자기 GPU에
    # 모델 전체를 하나씩 올리고 서로 다른 배치를 처리한 뒤 기울기만 주고받으므로
    # 거의 장수에 비례해 빨라진다. accelerate launch로 띄우면 LOCAL_RANK가 들어오니
    # 그 값을 보고 자기 몫의 GPU를 잡는다. 그냥 python으로 띄우면 LOCAL_RANK가 없어
    # 0번 한 장을 쓴다(기존 동작과 동일).
    local_rank = int(os.environ.get("LOCAL_RANK", 0))
    device_map = {"": local_rank} if torch.cuda.is_available() else "auto"
    world_size = int(os.environ.get("WORLD_SIZE", 1))
    if world_size > 1:
        effective = args.batch_size * args.grad_accum * world_size
        print(f"[train] DDP {world_size}개 프로세스 · 실효 배치 "
              f"{args.batch_size}x{args.grad_accum}x{world_size} = {effective}")
    import transformers
    _dtype_kw = _dtype_kwarg()
    model = AutoModelForCausalLM.from_pretrained(
        args.model, quantization_config=quant_config, device_map=device_map,
        **{_dtype_kw: compute_dtype},
    )
    model.config.use_cache = False

    # 의도한 dtype이 실제로 반영됐는지 바로 확인한다. 여기서 bfloat16이 찍히면
    # T4에서는 느린 에뮬레이션 경로로 돌고 있다는 뜻이므로 그냥 두면 안 된다.
    loaded_dtype = next(
        (p.dtype for p in model.parameters() if p.dtype.is_floating_point), None
    )
    print(f"[train] 모델 적재 dtype {loaded_dtype} (요청 {compute_dtype})")
    if loaded_dtype is not None and loaded_dtype != compute_dtype:
        print(f"[train] 경고: 요청한 dtype이 반영되지 않았습니다 "
              f"(transformers {transformers.__version__}, 인자명 {_dtype_kw}).")

    peft_config = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_r * 2,
        lora_dropout=0.05,
        bias="none",
        task_type="CAUSAL_LM",
        # attention과 MLP를 모두 잡는다. attention만 잡으면 JSON 스키마는 따라오지만
        # 한국어 군사 용어 표현이 베이스 모델 말투에서 잘 안 벗어난다.
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                        "gate_proj", "up_proj", "down_proj"],
    )

    def to_dataset(split: str) -> "Dataset":
        return Dataset.from_list([{"messages": r["messages"]} for r in load_sft(split)])

    kwargs = dict(
        output_dir=str(args.out),
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.batch_size,
        gradient_accumulation_steps=args.grad_accum,
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=0.03,
        logging_steps=10,
        # 평가 배치를 학습 배치와 같게 맞춘다. TrainingArguments 기본값이 8이라
        # 그냥 두면 평가 때만 배치가 4배로 뛴다. Qwen2.5는 어휘가 15만이라
        # loss 계산이 logits를 fp32로 올리는데(logits.float()), 배치 8 × 길이 2048 ×
        # 어휘 151936 × 4바이트 = 약 10GB가 한 번에 잡혀 T4 16GB에서 터진다.
        # Kaggle 실측: 1에폭 끝 평가 진입 직후 CUDA unspecified launch failure로 사망.
        per_device_eval_batch_size=args.batch_size,
        # 평가에서 logits/labels를 모아둘 이유가 없다. loss만 받으면 되므로 켜서
        # 평가 스텝마다 쌓이는 메모리를 없앤다.
        prediction_loss_only=True,
        # 에폭이 아니라 스텝 단위로 저장한다. 에폭 단위로 두면 transformers가
        # _maybe_log_save_evaluate에서 "평가 먼저, 저장 나중" 순서로 도는데,
        # 위의 평가가 터지면 저장에 도달하지 못해 그때까지의 학습이 통째로 날아간다
        # (Kaggle 실측: 5시간 50분치 손실). 주기적으로 저장해 두면 --resume이 살아난다.
        eval_strategy="steps",
        eval_steps=args.save_steps * 2,
        save_strategy="steps",
        save_steps=args.save_steps,
        save_total_limit=2,
        bf16=use_bf16,
        fp16=not use_bf16,
        gradient_checkpointing=True,
        # PEFT 어댑터와 함께 쓰면 reentrant 방식에서 "입력에 grad가 없다"며 역전파가
        # 끊긴다. 비reentrant 구현으로 명시해야 한다.
        gradient_checkpointing_kwargs={"use_reentrant": False},
        # 시스템 프롬프트가 길고 모든 샘플이 동일하다. 거기에 손실을 걸면 프롬프트
        # 암기에 용량이 쓰이므로 assistant 응답 구간에만 손실을 건다.
        completion_only_loss=True,
        # trl 1.4부터 loss_type 기본값이 "chunked_nll"로 바뀌었다. logits를 통째로
        # 만들지 않고 청크로 나눠 손실을 구해 메모리를 크게 아끼는데, 이 방식이
        # forward를 내부적으로 패치하다가 모델의 forward가 functools.partial로 감싸진
        # 경우를 못 다뤄 "'functools.partial' object has no attribute '__func__'"로
        # 죽는다(Kaggle 실측: trl 1.12.0 + device_map="auto").
        #
        # 기본값을 "nll"(패치 없는 예전 방식)로 두어 확실히 돌게 한다. 다만 Qwen2.5는
        # 어휘가 151936이라 nll은 logits를 fp32로 통째로 올리는 비용이 크다 —
        # 배치를 키우고 싶으면 --loss-type chunked_nll 로 시도해 볼 값어치가 있다
        # (단일 GPU로 바꾼 뒤로는 패치가 통과할 수도 있으나 아직 미검증).
        loss_type=args.loss_type,
        report_to=[],
        seed=args.seed,
    )
    # 인자 이름이 trl 버전마다 다르다 — 시퀀스 길이는 0.20에서 max_seq_length ->
    # max_length로, loss_type은 1.4 미만에는 아예 없다. 폐쇄망 서버에 어떤 버전이
    # 깔려 있을지 모르므로 설치된 버전이 실제로 받는 이름만 골라 넘긴다.
    import inspect
    accepted = inspect.signature(SFTConfig.__init__).parameters
    kwargs["max_length" if "max_length" in accepted else "max_seq_length"] = args.max_seq_len
    if "loss_type" not in accepted:
        kwargs.pop("loss_type", None)
    config = SFTConfig(**{k: v for k, v in kwargs.items() if k in accepted})
    trainer = SFTTrainer(
        model=model,
        args=config,
        train_dataset=to_dataset("train"),
        eval_dataset=to_dataset("valid"),
        processing_class=tokenizer,
        peft_config=peft_config,
    )

    # fp16 AMP의 GradScaler는 bf16 기울기를 다루지 못한다. 학습 대상(LoRA 어댑터)이
    # 어떤 경로로든 bf16으로 만들어지면 첫 기울기 갱신에서
    #   NotImplementedError: "_amp_foreach_non_finite_check_and_unscale_cuda"
    #                        not implemented for 'BFloat16'
    # 로 죽는다(Kaggle T4 x2 DDP 실측). QLoRA 표준 관행대로 학습 파라미터는 fp32로
    # 고정해 둔다 — 수치 안정성에도 이 편이 낫고, 어댑터만이라 메모리 부담도 없다.
    # 옵티마이저는 trainer.train() 안에서 만들어지므로 지금 바꿔도 안전하다.
    recast = 0
    for param in trainer.model.parameters():
        if param.requires_grad and param.dtype != torch.float32:
            param.data = param.data.to(torch.float32)
            recast += 1
    trainable = sum(p.numel() for p in trainer.model.parameters() if p.requires_grad)
    print(f"[train] 학습 파라미터 {trainable / 1e6:.1f}M "
          f"(fp32로 캐스팅한 텐서 {recast}개)")
    # Colab 무료 티어는 세션이 끊길 수 있다. 에폭마다 저장해 두고, 체크포인트가 있으면
    # 처음부터 다시 돌리지 않고 이어서 학습한다.
    checkpoints = sorted(args.out.glob("checkpoint-*")) if args.out.exists() else []
    if checkpoints:
        print(f"[train] 체크포인트 발견({checkpoints[-1].name}) — 이어서 학습합니다.")
    trainer.train(resume_from_checkpoint=bool(checkpoints))

    adapter_dir = args.out / "adapter"
    trainer.model.save_pretrained(adapter_dir)
    tokenizer.save_pretrained(adapter_dir)
    print(f"어댑터 저장: {adapter_dir}")
    print("다음 단계: python finetune/evaluate.py --backend hf "
          f"--model {args.model} --adapter {adapter_dir}")


def merge(args: argparse.Namespace) -> None:
    """어댑터를 베이스에 병합해 통짜 가중치로 저장한다.

    vLLM/Ollama로 폐쇄망 서빙할 때 어댑터를 따로 얹는 것보다 병합본이 다루기 쉽다.
    병합은 fp16으로 해야 하며(4bit 로드 상태에서는 병합할 수 없다), 병합 후 서빙
    단계에서 다시 4bit/GGUF로 양자화해 기획서의 2~4GB 목표를 맞춘다.
    """
    from finetune import compat

    # 사전 조건은 무거운 임포트(torch/transformers)보다 먼저 본다. 어댑터가 없거나
    # 디스크가 모자란 것을 몇 십 초 기다린 뒤에 알 이유가 없다.
    adapter_dir = args.out / "adapter"
    if not (adapter_dir / "adapter_config.json").exists():
        raise SystemExit(
            f"어댑터가 없습니다: {adapter_dir}\n"
            "먼저 학습을 끝내야 병합할 수 있습니다 (train_lora.py를 --merge 없이 실행)."
        )

    # 병합본은 fp16 통짜라 1.5B가 약 3GB, 3B가 약 6GB다. 디스크가 모자라면 절반쯤
    # 쓰다가 죽어 쓸모없는 파일만 남으므로 미리 확인한다.
    params_b = 1.5
    for hint, size in (("0.5b", 0.5), ("1.5b", 1.5), ("3b", 3.0), ("7b", 7.0)):
        if hint in args.model.lower():
            params_b = size
    need_gb = params_b * 2 * 1.15   # fp16 2바이트/파라미터 + 여유
    args.merge.parent.mkdir(parents=True, exist_ok=True)
    free_gb = compat.free_disk_gb(str(args.merge.parent))
    print(f"[merge] 예상 필요 용량 {need_gb:.1f}GB · 여유 {free_gb:.1f}GB")
    if free_gb < need_gb:
        raise SystemExit(
            f"디스크 여유가 부족합니다 (필요 {need_gb:.1f}GB, 남음 {free_gb:.1f}GB).\n"
            f"중간 체크포인트를 지우면 확보됩니다: rm -rf {args.out}/checkpoint-*"
        )

    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    # 베이스를 fp16으로 올려야 병합이 된다(4bit 가중치에는 LoRA를 합칠 수 없다).
    # 그러면 대상이 평범한 nn.Linear라서 PEFT가 torchao 디스패처를 먼저 시도하는데,
    # 그 버전 검사가 예외를 던지는 환경이 있어 미리 막아 둔다. 자세한 내용은 compat 참고.
    compat.patch_peft_torchao_check()
    from peft import PeftModel

    base = AutoModelForCausalLM.from_pretrained(
        args.model, **{_dtype_kwarg(): torch.float16}, device_map="cpu"
    )
    merged = PeftModel.from_pretrained(base, adapter_dir).merge_and_unload()
    merged.save_pretrained(args.merge)
    AutoTokenizer.from_pretrained(args.model).save_pretrained(args.merge)
    embed_chat_template(args.merge)
    print(f"병합 저장: {args.merge}")


def embed_chat_template(model_dir: Path) -> bool:
    """채팅 템플릿을 tokenizer_config.json 안에 넣어 준다.

    transformers 4.56부터 save_pretrained가 템플릿을 tokenizer_config.json 밖의
    chat_template.jinja 파일로 빼서 저장한다. 그런데 추론 서버(TGI, vLLM, Ollama)는
    tokenizer_config.json의 chat_template 키만 읽으므로, 그대로 서빙하면 채팅 요청이
    "Template error: template not found"로 전부 실패한다. 학습 자체는 멀쩡한데 배포만
    죽는 형태라 원인을 찾기 어렵다 — 저장 직후에 되돌려 놓는다.
    파일은 지우지 않는다(transformers 쪽 경로는 그대로 두는 편이 안전하다).
    """
    cfg_path = model_dir / "tokenizer_config.json"
    jinja_path = model_dir / "chat_template.jinja"
    if not cfg_path.exists():
        return False
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if cfg.get("chat_template"):
        return False
    if not jinja_path.exists():
        print("[warn] chat_template.jinja가 없어 템플릿을 심지 못했습니다.")
        return False
    cfg["chat_template"] = jinja_path.read_text(encoding="utf-8")
    cfg_path.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[merge] chat_template을 tokenizer_config.json에 심었습니다.")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--data", type=Path, default=None,
                    help="SFT 데이터 디렉터리. 기본값 finetune/data. "
                         "gen_dataset.py --layout-target으로 만든 데이터를 학습할 때 "
                         "finetune/data_layout 처럼 지정한다.")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out")
    ap.add_argument("--epochs", type=float, default=3.0)
    ap.add_argument("--batch-size", type=int, default=2)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--lora-r", type=int, default=16)
    ap.add_argument("--max-seq-len", type=int, default=2048)
    ap.add_argument("--loss-type", default="nll", choices=["nll", "chunked_nll"],
                    help="chunked_nll은 어휘가 큰 모델에서 메모리를 크게 아끼지만 "
                         "trl 1.12에서 4bit+PEFT 조합과 충돌한 전력이 있다. 기본값 nll이 안전.")
    ap.add_argument("--save-steps", type=int, default=25,
                    help="체크포인트 저장 주기(스텝). 세션이 끊기거나 죽어도 여기까지는 "
                         "남아 --resume으로 이어갈 수 있다. 평가는 이 값의 2배 주기로 돈다.")
    ap.add_argument("--seed", type=int, default=20260829)
    ap.add_argument("--dry-run", action="store_true",
                    help="torch 없이 데이터셋 스키마와 토큰 길이만 점검한다.")
    ap.add_argument("--merge", type=Path, default=None,
                    help="학습된 어댑터를 베이스에 병합해 이 경로에 저장한다.")
    args = ap.parse_args()

    if args.data:
        global DATA_DIR
        DATA_DIR = args.data

    if args.dry_run:
        dry_run(args)
    elif args.merge:
        merge(args)
    else:
        train(args)


if __name__ == "__main__":
    main()

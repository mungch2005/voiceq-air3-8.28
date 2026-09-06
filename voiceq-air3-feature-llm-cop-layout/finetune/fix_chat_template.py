"""이미 허브에 올라간 모델 저장소의 채팅 템플릿을 제자리에 심어 준다.

transformers 4.56부터 tokenizer.save_pretrained()가 채팅 템플릿을
tokenizer_config.json이 아니라 chat_template.jinja라는 별도 파일로 저장한다.
그런데 TGI/vLLM/Ollama 같은 추론 서버는 tokenizer_config.json의 chat_template
키만 읽으므로, 그 상태로 배포하면 /v1/chat/completions 요청이 전부
"Template error: template not found" (HTTP 422)로 실패한다.

train_lora.py는 이제 병합 직후에 이걸 스스로 처리하지만(embed_chat_template),
그 전에 학습해서 올려 둔 저장소는 이 스크립트로 한 번 고쳐 주면 된다.

사용법 (쓰기 권한이 있는 토큰 필요):
    pip install -U huggingface_hub
    huggingface-cli login          # write 권한 토큰
    python finetune/fix_chat_template.py Jun13KU/voicecue-qwen2.5-3b

고친 뒤에는 추론 엔드포인트를 한 번 재시작(Pause -> Resume)해야 한다.
서버가 시작할 때만 저장소를 읽기 때문이다.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

from huggingface_hub import HfApi, hf_hub_download


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_id", help="예: Jun13KU/voicecue-qwen2.5-3b")
    args = ap.parse_args()

    api = HfApi()
    files = api.list_repo_files(args.repo_id)
    if "chat_template.jinja" not in files:
        raise SystemExit(
            "chat_template.jinja가 저장소에 없습니다. 템플릿 원본이 없으면 심을 수 없습니다."
        )

    cfg_path = Path(hf_hub_download(args.repo_id, "tokenizer_config.json"))
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    if cfg.get("chat_template"):
        print("이미 tokenizer_config.json에 chat_template이 들어 있습니다. 할 일 없음.")
        return

    jinja = Path(hf_hub_download(args.repo_id, "chat_template.jinja")).read_text(
        encoding="utf-8"
    )
    cfg["chat_template"] = jinja

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "tokenizer_config.json"
        out.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        api.upload_file(
            path_or_fileobj=str(out),
            path_in_repo="tokenizer_config.json",
            repo_id=args.repo_id,
            commit_message="Embed chat template in tokenizer_config for inference servers",
        )
    print(
        f"완료: {args.repo_id}\n"
        "추론 엔드포인트를 Pause -> Resume 해서 다시 읽게 하세요."
    )


if __name__ == "__main__":
    main()

"""파인튜닝 병합본을 허깅페이스 허브에 올린다.

kaggle_train.ipynb 10번 셀과 하는 일은 같지만, 그 노트북 실행 중이 아닌 다른 곳에서
(로컬 PC, 이미 끝난 실행의 Output을 붙인 새 Kaggle 세션 등) 올릴 때 쓴다 — 노트북에
업로드 셀이 없던 예전 실행 결과를 처음부터 다시 학습할 필요 없이 그대로 올릴 수 있다.

토큰은 세 군데에서 순서대로 찾는다: --token 인자, HF_TOKEN 환경변수, (Kaggle 노트북
안이라면) Kaggle Secrets의 HF_TOKEN. 셋 다 없으면 뭘 해야 하는지 알려주고 멈춘다.

사용법:
    python finetune/upload_to_hub.py finetune/out/merged Jun13KU/voicecue-qwen2.5-3b
    python finetune/upload_to_hub.py <병합본 경로> <계정>/<저장소명> --token hf_xxx
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path


def resolve_token(explicit: str | None) -> str:
    if explicit:
        return explicit

    env = os.environ.get("HF_TOKEN")
    if env:
        return env

    try:
        from kaggle_secrets import UserSecretsClient

        token = UserSecretsClient().get_secret("HF_TOKEN")
        if token:
            return token
    except Exception:
        pass

    raise SystemExit(
        "HF 토큰을 못 찾았습니다. 다음 중 하나로 넘기세요:\n"
        "  --token hf_xxx\n"
        "  환경변수 HF_TOKEN\n"
        "  (Kaggle 노트북 안이면) Add-ons -> Secrets에 HF_TOKEN으로 등록 + 이 노트북에 Attach\n"
        "토큰은 대상 저장소에 Write 권한이 있는 fine-grained 토큰이어야 합니다."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("folder", type=Path, help="올릴 병합본 폴더 (예: finetune/out/merged)")
    ap.add_argument("repo_id", help="예: Jun13KU/voicecue-qwen2.5-3b")
    ap.add_argument("--token", default=None, help="HF 토큰. 안 주면 환경변수/Kaggle Secrets에서 찾는다.")
    ap.add_argument("--message", default="파인튜닝 결과 업로드", help="커밋 메시지")
    ap.add_argument("--private", action="store_true", help="새로 만드는 저장소를 비공개로 생성")
    args = ap.parse_args()

    if not args.folder.is_dir():
        raise SystemExit(
            f"{args.folder} 가 디렉터리가 아니거나 없습니다. "
            "train_lora.py --merge 로 만든 병합본 폴더를 가리키는지 확인하세요."
        )
    if not any(args.folder.glob("*.safetensors")) and not any(args.folder.glob("*.bin")):
        print(
            f"경고: {args.folder} 안에 가중치 파일(.safetensors/.bin)이 안 보입니다. "
            "잘못된 경로일 수 있습니다. 계속 진행합니다."
        )

    token = resolve_token(args.token)

    from huggingface_hub import HfApi

    api = HfApi(token=token)
    api.create_repo(args.repo_id, exist_ok=True, private=args.private)
    api.upload_folder(
        folder_path=str(args.folder),
        repo_id=args.repo_id,
        commit_message=args.message,
    )
    print(f"업로드 완료: https://huggingface.co/{args.repo_id}")
    print(
        "HF Inference Endpoint를 쓰고 있다면 Settings > Advanced > commit revision을 "
        "방금 올린 커밋으로 바꿔야 반영됩니다 (Pause/Resume만으로는 이전 캐시를 그대로 씁니다)."
    )


if __name__ == "__main__":
    main()

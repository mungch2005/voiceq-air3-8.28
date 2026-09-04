"""검수용 데이터 export — turns_*.jsonl 전체를 사람이 훑어보기 좋은 압축 JSON으로 만든다.

sft_*.jsonl은 검수용으로 쓰기에 적합하지 않다. 같은 턴을 FAST/FULL 두 번 담고,
매번 동일한 시스템 프롬프트(수백 토큰)를 반복하기 때문이다. turns_*.jsonl에서
검수에 필요한 필드만 뽑아 하나의 배열로 합친다 — HTML 뷰어에 그대로 박아 넣을
크기로 줄이는 것이 목적이다(fast_user/full_user처럼 템플릿에서 기계적으로 파생되는
큰 텍스트는 제외).

사용:
    python finetune/export_review.py                    # finetune/data/review.json 생성
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules import organization as org  # noqa: E402
from modules import playbook as pb  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "data"


def compact(turn: dict, split: str, row_id: int) -> dict:
    entry = turn["full_target"]["operation_log_entry"]
    return {
        "id": row_id,
        "split": split,
        "scenario_id": turn.get("scenario_id", ""),
        "turn_index": turn.get("turn_index", 0),
        "speaker": turn["speaker"],
        "utterance": turn["utterance"],
        "situation": turn["fast_target"]["situation"]["type"],
        "reason": turn["fast_target"]["situation"]["reason"],
        "kind": entry["kind"],
        "event_id": entry["event_id"],
        "content": entry["content"],
        "memory_before": turn["state_in"]["context_memory"],
        "memory_after": turn["full_target"]["context_memory"],
        "corrections": turn["state_in"]["user_corrections"],
        "board": turn["full_target"]["situation_board"],
        "cop_panels": turn["cop_reference"]["panels"],
        "cop_unresolved": turn["cop_reference"]["unresolved_slots"],
        "prior_open_events": [
            {"event_id": e["event_id"], "title": e["title"]}
            for e in turn["state_in"]["operation_log"]
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, default=DATA_DIR / "review.json")
    args = ap.parse_args()

    rows = []
    row_id = 0
    for split in ("train", "valid", "test"):
        path = DATA_DIR / f"turns_{split}.jsonl"
        if not path.exists():
            raise SystemExit(f"{path} 가 없습니다. 먼저 python finetune/gen_dataset.py 를 실행하세요.")
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            rows.append(compact(json.loads(line), split, row_id))
            row_id += 1

    # 화자 설명·상황 유형 목록도 함께 담는다 — 리뷰 화면이 실제 프롬프트가 화자에게
    # 주입하는 계급·담당분야·영향력 설명을 그대로 보여줘야, "기타 지휘관 발언이 단독으로
    # 상황을 뒤집지 않았는가" 같은 판단을 검수자가 코드를 열어보지 않고도 할 수 있다.
    speakers = {s["title"]: org.describe_speaker(s["title"]) for s in org.speakers()}
    situations = [
        {"name": s["name"], "keywords": s.get("keywords", [])}
        for s in pb.load_playbook()["situations"]
    ]

    payload = {
        "meta": {"rows": len(rows)},
        "speakers": speakers,
        "situations": situations,
        "rows": rows,
    }
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    args.out.write_text(text, encoding="utf-8")
    print(f"{len(rows)}건 -> {args.out} ({len(text) / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

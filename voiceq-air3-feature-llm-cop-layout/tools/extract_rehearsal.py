"""파인튜닝 데이터셋의 시나리오를 시연 페이지가 재생할 대본 + 굽기 파일로 뽑는다.

시연 리허설에 쓸 대본은 손으로 지어내지 않는다. finetune/gen_dataset.py가 만드는
데이터셋에는 발언마다 정답 판단(fast_target / full_target)이 함께 들어 있고, 그 형식이
앱의 context_memory.apply_fast_result / apply_full_result가 받는 것과 같다. 그대로
옮기면 LLM도 네트워크도 없이 "정답대로 도는" 리허설이 된다.

데이터셋은 시드로 재현된다(기본 20260829). 먼저 만든 뒤 여기에 넘긴다:

    python finetune/gen_dataset.py --out /tmp/ds
    python tools/extract_rehearsal.py --dataset /tmp/ds \
        --pick train-094:drone:"드론상황 — 유도로 상공 소형 드론" \
        --pick valid-019:intrusion:"미상인원 기지침투 — 남측초소 식별"

런타임에는 쓰이지 않는다. 결과(data/sample_dialogues/*.json)만 커밋한다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "data" / "sample_dialogues"


def load_turns(dataset: Path) -> dict[str, dict[int, dict]]:
    """turns_*.jsonl 전체를 {시나리오 id: {턴 번호: 레코드}}로 읽는다."""
    rows: dict[str, dict[int, dict]] = {}
    for path in sorted(dataset.glob("turns_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            rows.setdefault(record["scenario_id"], {})[record["turn_index"]] = record
    return rows


def _as_dict(value) -> dict:
    return json.loads(value) if isinstance(value, str) else dict(value)


def extract(records: dict[int, dict], out_id: str, name: str, source: str) -> tuple[Path, Path]:
    """한 시나리오를 대본 파일과 굽기 파일로 쓴다."""
    turns = [records[i] for i in sorted(records)]

    script = {
        "name": name,
        "source": source,
        "note": (
            "finetune 데이터셋에서 뽑은 대본이다. audio는 data/sample_dialogues/audio/ "
            "아래의 녹음 파일이며, 없으면 자막만 나온다."
        ),
        "turns": [
            {
                "speaker": t["speaker"],
                "utterance": t["utterance"],
                "audio": f"{out_id}-{i + 1}.m4a",
            }
            for i, t in enumerate(turns)
        ],
    }
    script_path = OUT_DIR / f"{out_id}.json"
    script_path.write_text(
        json.dumps(script, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    baked = {
        "note": (
            "데이터셋의 정답 판단(fast_target / full_target)을 그대로 옮긴 것이다. "
            "모델을 실제로 호출해 구운 것이 아니므로 display_latency는 없다 — "
            "실측 지연이 필요하면 시연 페이지의 '시나리오 굽기'로 다시 구우면 된다."
        ),
        "model": f"정답 판단 ({source})",
        "baked_at": "",
        "turns": [
            {
                "speaker": t["speaker"],
                "utterance": t["utterance"],
                "fast": _as_dict(t["fast_target"]),
                "full": _as_dict(t["full_target"]),
                "display_latency": None,
            }
            for t in turns
        ],
    }
    baked_path = OUT_DIR / f"{out_id}.baked.json"
    baked_path.write_text(
        json.dumps(baked, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return script_path, baked_path


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", type=Path, required=True,
                    help="turns_*.jsonl 이 있는 디렉터리")
    ap.add_argument("--pick", action="append", required=True,
                    help="시나리오id:출력id:표시이름")
    args = ap.parse_args()

    rows = load_turns(args.dataset)
    for spec in args.pick:
        source_id, out_id, name = spec.split(":", 2)
        if source_id not in rows:
            raise SystemExit(f"데이터셋에 {source_id} 가 없습니다")
        script_path, baked_path = extract(rows[source_id], out_id, name, source_id)
        print(f"{source_id} -> {script_path.name}, {baked_path.name} "
              f"({len(rows[source_id])}턴)")


if __name__ == "__main__":
    main()

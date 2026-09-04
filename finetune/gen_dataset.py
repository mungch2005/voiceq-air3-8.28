"""VOICE-CUE 파인튜닝 데이터셋 생성기 (기획서 3-나 1단계 / 4-다①).

기획서 4-다①이 요구하는 라벨 네 가지를 한 발언(턴)마다 모두 붙인다.
    ⓐ 발화자 구분·영향력 태그 → organization.json의 직책이 프롬프트에 주입됨
    ⓑ 주요사태(MSEL) 분류      → operation_log_entry.kind / event_id
    ⓒ 사태별 중요도·긴급도      → situation_board[].rank / urgency
    ⓓ 화면 구성(COP) JSON 정답  → cop_reference (아래 설명)

ⓓ에 대한 설계 결정 — LLM은 COP JSON을 직접 출력하지 않는다.
    이 저장소는 화면 배치를 LLM이 아니라 운용자 플레이북(cop_playbook.json)이
    결정하도록 이미 설계돼 있다(CLAUDE.md 11절: 폐쇄 카탈로그를 거치지 않는 변경
    금지). 모델이 source_id를 지어내면 존재하지 않는 화면을 띄우라는 명령이 되고,
    기획서 3-라② "전문가 정답 레이아웃 대비 일치율" 지표도 흔들린다.
    그래서 학습 타깃은 FAST(상황 유형)/FULL(기록) 두 갈래로 두고, COP JSON 정답은
    상황 유형에서 playbook.build_layout()으로 결정론적으로 파생시켜 cop_reference에
    함께 저장한다. evaluate.py는 이 값을 지표 ②의 정답으로 쓴다.

프롬프트는 modules/prompts.py의 build_fast_turn/build_full_turn을 그대로 호출해서
만든다. 학습 때와 서빙 때 프롬프트가 한 글자라도 다르면 파인튜닝 효과가 사라지므로,
문자열을 복제하지 않고 런타임과 같은 함수를 쓴다.

사용:
    python finetune/gen_dataset.py                  # 기본 120 시나리오
    python finetune/gen_dataset.py --scenarios 200 --seed 7
    python finetune/gen_dataset.py --few-shot       # few-shot 예시를 포함해 생성
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from finetune import scenario_bank as bank  # noqa: E402
from modules import organization as org  # noqa: E402
from modules import playbook as pb  # noqa: E402
from modules import prompts  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "data"

URGENCY_ORDER = {"긴급": 0, "주의": 1, "관찰": 2}
IGNORE_REASON = "상황 판단·조치와 무관한 발언이므로 진행 중인 상황 유형을 유지"


# ---------------------------------------------------------------------
# 슬롯 채우기
# ---------------------------------------------------------------------

def make_context(rng: random.Random, situation_name: str) -> dict:
    """한 사태 안에서는 방위·시설·대상이 바뀌면 안 된다. 사태 단위로 한 번만 뽑는다."""
    spec = bank.SITUATIONS[situation_name]
    az = rng.choice(bank.AZIMUTHS)
    az2 = rng.choice([a for a in bank.AZIMUTHS if a != az])
    n = rng.randint(1, 5)
    return {
        "az": az,
        "az2": az2,
        "fac": rng.choice(spec["facilities"]),
        "obj": rng.choice(spec["objects"]),
        "n": n,
        "m": max(1, n - rng.randint(0, n - 1)) if n > 1 else 1,
        "min": rng.choice([3, 5, 10, 15, 20, 30]),
        "dist": rng.choice([40, 60, 80, 120, 800, 1200, 1500]),
        "km": rng.choice(["0.8", "1.5", "2.0", "3.0"]),
        "ms": rng.choice([2, 3, 5, 7]),
    }


def fill(text: str, ctx: dict) -> str:
    return text.format(**ctx)


# ---------------------------------------------------------------------
# 시나리오 상태 기계
# ---------------------------------------------------------------------

class ScenarioState:
    """한 회의(시나리오) 동안 누적되는 상태.

    modules/context_memory.py가 런타임에 유지하는 것과 같은 모양을 유지한다.
    특히 operation_log는 prompts.format_open_events()가 그대로 읽을 수 있어야 한다.
    """

    def __init__(self) -> None:
        self.memory_lines: list[str] = []
        self.board: list[dict] = []       # {event_id, event, urgency, touched}
        self.op_log: list[dict] = []      # {event_id, title, entries[]}
        self.corrections: list[str] = []
        self.situation_type: str = ""
        self.clock = 14 * 3600            # 14:00:00에서 시작
        self.tick = 0

    def timestamp(self) -> str:
        self.clock += random.Random(self.clock).randint(20, 90)
        h, rem = divmod(self.clock, 3600)
        m, s = divmod(rem, 60)
        return f"{h % 24:02d}:{m:02d}:{s:02d}"

    # --- 스냅샷 (이번 턴 프롬프트에 들어갈 "입력" 상태) ---
    def context_memory(self) -> str:
        if not self.memory_lines:
            return ""
        return ". ".join(self.memory_lines[-3:]) + "."

    def board_snapshot(self) -> list[dict]:
        """긴급도 우선, 같으면 최근 갱신 순으로 rank를 매긴다. 최대 5개(프롬프트 규칙)."""
        ordered = sorted(
            self.board,
            key=lambda e: (URGENCY_ORDER.get(e["urgency"], 9), -e["touched"]),
        )[:5]
        return [
            {"rank": i + 1, "event": e["event"], "urgency": e["urgency"]}
            for i, e in enumerate(ordered)
        ]

    # --- 갱신 ---
    def open_event(self, event: str, urgency: str, mem: str, log: str,
                   speaker: str, ts: str) -> str:
        event_id = f"사태{len(self.op_log) + 1}"
        self.tick += 1
        self.board.append(
            {"event_id": event_id, "event": event, "urgency": urgency, "touched": self.tick}
        )
        self.op_log.append(
            {"event_id": event_id, "title": log,
             "entries": [{"timestamp": ts, "speaker": speaker, "detail": log}]}
        )
        self.memory_lines.append(mem)
        return event_id

    def update_event(self, event_id: str, event: str, urgency: str | None, mem: str,
                     log: str, speaker: str, ts: str) -> None:
        self.tick += 1
        for e in self.board:
            if e["event_id"] == event_id:
                e["event"] = event
                e["touched"] = self.tick
                if urgency:
                    e["urgency"] = urgency
                break
        for ev in self.op_log:
            if ev["event_id"] == event_id:
                ev["entries"].append({"timestamp": ts, "speaker": speaker, "detail": log})
                break
        self.memory_lines.append(mem)


# ---------------------------------------------------------------------
# 턴 생성
# ---------------------------------------------------------------------

def emit_turn(state: ScenarioState, rng: random.Random, speaker: str, utterance: str,
              situation_type: str, reason: str, log_entry: dict) -> dict:
    """프롬프트 입력 상태를 먼저 스냅샷하고, 그 다음 상태를 갱신한 결과를 정답으로 삼는다.

    순서가 중요하다. 기획서 4-③ "Context Memory를 가장 먼저 갱신하고 그것을 바탕으로
    나머지를 산출"과 같은 순서여야 학습 데이터가 런타임 동작과 일치한다.
    """
    state_in = {
        "context_memory": state.context_memory(),
        "user_corrections": list(state.corrections),
        "operation_log": json.loads(json.dumps(state.op_log, ensure_ascii=False)),
    }
    fast_user = prompts.build_fast_turn(
        state_in["context_memory"], state_in["user_corrections"],
        org.describe_speaker(speaker), utterance, pb.describe_for_llm(),
    )
    full_user = prompts.build_full_turn(
        state_in["context_memory"], state_in["user_corrections"],
        org.describe_speaker(speaker), utterance, state_in["operation_log"],
    )
    layout, unresolved = pb.build_layout(situation_type, utterance)
    return {
        "speaker": speaker,
        "utterance": utterance,
        "state_in": state_in,
        "fast_user": fast_user,
        "full_user": full_user,
        "fast_target": {"situation": {"type": situation_type, "reason": reason}},
        "full_target": {
            "context_memory": "",       # 갱신 후 채운다
            "situation_board": [],
            "operation_log_entry": log_entry,
        },
        "cop_reference": {
            "situation": situation_type,
            "source_ids": [item["source_id"] for item in layout],
            # 화면 이름만으로는 검수가 안 된다. 어느 자리에 얼마만 한 크기로 뜨는지가
            # 기획서 3-라② "핵심정보 상위배치"의 실체이므로 자리·크기까지 함께 남긴다.
            "panels": [
                {
                    "priority": item["priority"],
                    "name": item["name"],
                    "grid": list(item["grid"]),
                    "position": item["position"],
                    "origin": item["origin"],
                    "slot": item["slot"],
                }
                for item in layout
            ],
            "unresolved_slots": unresolved,
        },
    }


def build_scenario(rng: random.Random, primary_situation: str) -> list[dict]:
    state = ScenarioState()
    turns: list[dict] = []

    situation_names = list(bank.SITUATIONS)
    n_events = 1 if rng.random() < 0.6 else 2
    events = [primary_situation]
    if n_events == 2:
        events.append(rng.choice([s for s in situation_names if s != primary_situation]))

    for event_index, situation_name in enumerate(events):
        spec = bank.SITUATIONS[situation_name]
        ctx = make_context(rng, situation_name)

        # --- 사태 개시 (kind="상황") ---
        tpl = rng.choice(spec["openers"])
        speaker = rng.choice(tpl["spk"])
        utterance = fill(tpl["u"], ctx)
        log = fill(tpl["log"], ctx)
        board_text = fill(tpl["board"], ctx)
        ts = state.timestamp()

        turn = emit_turn(state, rng, speaker, utterance, situation_name, board_text,
                         {"kind": "상황", "event_id": f"사태{len(state.op_log) + 1}", "content": log})
        event_id = state.open_event(board_text, tpl.get("urg", "주의"),
                                    fill(tpl["mem"], ctx), log, speaker, ts)
        state.situation_type = situation_name
        turn["full_target"]["context_memory"] = state.context_memory()
        turn["full_target"]["situation_board"] = state.board_snapshot()
        turns.append(turn)

        # --- 후속 조치 ---
        followups = rng.sample(spec["followups"], k=min(len(spec["followups"]), rng.randint(2, 4)))
        for tpl in followups:
            # 잡담(kind="무시") — 상황 유형도 일지도 건드리면 안 되는 턴.
            if rng.random() < 0.18:
                chat = rng.choice(bank.CHATTER)
                chat_speaker = rng.choice(chat["spk"])
                chat_turn = emit_turn(
                    state, rng, chat_speaker, chat["u"], state.situation_type, IGNORE_REASON,
                    {"kind": "무시", "event_id": "", "content": ""},
                )
                chat_turn["full_target"]["context_memory"] = state.context_memory()
                chat_turn["full_target"]["situation_board"] = state.board_snapshot()
                turns.append(chat_turn)

            # 배치 제약조건 복합 명령문 — 유형은 유지, 기존 사태의 조치로 기록, 보정 규칙 누적.
            if rng.random() < 0.15:
                con = rng.choice(bank.CONSTRAINT_TURNS)
                con_speaker = rng.choice(con["spk"])
                con_u = fill(con["u"], ctx)
                con_log = fill(con["log"], ctx)
                con_board = fill(con["board"], ctx)
                con_ts = state.timestamp()
                con_turn = emit_turn(
                    state, rng, con_speaker, con_u, state.situation_type, con_board,
                    {"kind": "조치", "event_id": event_id, "content": con_log},
                )
                state.update_event(event_id, con_board, None, fill(con["mem"], ctx),
                                   con_log, con_speaker, con_ts)
                con_turn["full_target"]["context_memory"] = state.context_memory()
                con_turn["full_target"]["situation_board"] = state.board_snapshot()
                turns.append(con_turn)
                if con["rule"] not in state.corrections:
                    state.corrections.append(con["rule"])

            speaker = rng.choice(tpl["spk"])
            utterance = fill(tpl["u"], ctx)
            log = fill(tpl["log"], ctx)
            board_text = fill(tpl["board"], ctx)
            ts = state.timestamp()
            turn = emit_turn(state, rng, speaker, utterance, situation_name, board_text,
                             {"kind": "조치", "event_id": event_id, "content": log})
            state.update_event(event_id, board_text, tpl.get("urg"), fill(tpl["mem"], ctx),
                               log, speaker, ts)
            turn["full_target"]["context_memory"] = state.context_memory()
            turn["full_target"]["situation_board"] = state.board_snapshot()
            turns.append(turn)

        if event_index == 0 and len(events) > 1:
            # 두 번째 사태로 넘어갈 때 유형이 바뀐다. "새로운 종류의 사건일 때만 유형을
            # 바꾼다"는 FAST 프롬프트 규칙을 지키는 양성 예시가 여기서 만들어진다.
            state.situation_type = ""

    return turns


# ---------------------------------------------------------------------
# SFT 변환 · 검증 · 출력
# ---------------------------------------------------------------------

def to_sft(turn: dict, route: str, few_shot: bool) -> dict:
    if route == "fast":
        system, user, target = prompts.FAST_SYSTEM_PROMPT, turn["fast_user"], turn["fast_target"]
        shots = prompts.FAST_FEW_SHOT_MESSAGES
    else:
        system, user, target = prompts.FULL_SYSTEM_PROMPT, turn["full_user"], turn["full_target"]
        shots = prompts.FULL_FEW_SHOT_MESSAGES

    messages = [{"role": "system", "content": system}]
    if few_shot:
        messages += [dict(m) for m in shots]
    messages.append({"role": "user", "content": user})
    messages.append({"role": "assistant", "content": json.dumps(target, ensure_ascii=False, indent=2)})
    return {"route": route, "messages": messages}


def validate(turns: list[dict]) -> None:
    """생성 즉시 라벨을 검증한다. 깨진 라벨로 학습을 돌리면 원인을 찾기가 훨씬 어렵다."""
    valid_situations = set(pb.situation_names())
    for i, turn in enumerate(turns):
        sit = turn["fast_target"]["situation"]["type"]
        assert sit in valid_situations, f"turn {i}: 플레이북에 없는 상황 유형 {sit!r}"
        assert turn["fast_target"]["situation"]["reason"], f"turn {i}: reason 비어 있음"

        entry = turn["full_target"]["operation_log_entry"]
        assert entry["kind"] in ("상황", "조치", "무시"), f"turn {i}: kind={entry['kind']!r}"
        if entry["kind"] == "무시":
            assert entry["event_id"] == "" and entry["content"] == "", f"turn {i}: 무시인데 내용 있음"
        else:
            assert entry["content"], f"turn {i}: {entry['kind']}인데 content 비어 있음"
        if entry["kind"] == "조치":
            known = {e["event_id"] for e in turn["state_in"]["operation_log"]}
            assert entry["event_id"] in known, f"turn {i}: 조치가 없는 사태 {entry['event_id']} 지목"

        board = turn["full_target"]["situation_board"]
        assert len(board) <= 5, f"turn {i}: 상황판 5개 초과"
        assert [b["rank"] for b in board] == list(range(1, len(board) + 1)), f"turn {i}: rank 불연속"
        for b in board:
            assert b["urgency"] in URGENCY_ORDER, f"turn {i}: urgency={b['urgency']!r}"

        assert turn["cop_reference"]["source_ids"], f"turn {i}: COP 정답 레이아웃이 비어 있음"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8"
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenarios", type=int, default=220)
    ap.add_argument("--seed", type=int, default=20260829)
    ap.add_argument("--few-shot", action="store_true",
                    help="SFT 샘플에 few-shot 예시를 포함한다. 기본값은 제외 — "
                         "파인튜닝의 목적이 few-shot 없이도 형식을 지키게 만들어 "
                         "입력 토큰과 지연시간을 줄이는 것이기 때문이다.")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    situation_names = list(bank.SITUATIONS)

    by_situation: dict[str, list[list[dict]]] = {name: [] for name in situation_names}
    for i in range(args.scenarios):
        # 라운드로빈으로 주 상황을 돌려 11개 유형이 고르게 학습되도록 한다.
        primary = situation_names[i % len(situation_names)]
        by_situation[primary].append(build_scenario(rng, primary))

    # 상황 유형별로 나눠 담는다(계층화). 그냥 섞어서 자르면 시연 핵심인 드론상황이
    # 평가 셋에서 통째로 빠지는 일이 생긴다 — 실제로 처음 생성 때 그랬다.
    # 시나리오 단위로 갈라야 같은 회의의 앞뒤 턴이 train/test에 흩어지지 않는다.
    splits: dict[str, list[list[dict]]] = {"valid": [], "test": [], "train": []}
    for name in situation_names:
        group = by_situation[name]
        rng.shuffle(group)
        n_valid = max(1, round(len(group) * 0.10))
        n_test = max(1, round(len(group) * 0.10))
        splits["valid"] += group[:n_valid]
        splits["test"] += group[n_valid:n_valid + n_test]
        splits["train"] += group[n_valid + n_test:]
    for scen_list in splits.values():
        rng.shuffle(scen_list)

    args.out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for split, scen_list in splits.items():
        turns = []
        for scen_idx, scen in enumerate(scen_list):
            # 검수용 식별자. 학습에는 쓰이지 않는다(to_sft가 route/fast_target/full_target/
            # fast_user/full_user만 읽는다) — 같은 회의(시나리오)의 턴을 리뷰 화면에서
            # 묶어 보여주기 위한 것이다. turns_*.jsonl 안에서 같은 시나리오의 턴은 이미
            # 붙어 있으므로(스크립트가 시나리오 단위로만 섞는다) 순서는 그대로 유지된다.
            for turn_idx, t in enumerate(scen):
                t["scenario_id"] = f"{split}-{scen_idx:03d}"
                t["turn_index"] = turn_idx
                turns.append(t)
        validate(turns)
        write_jsonl(args.out / f"turns_{split}.jsonl", turns)

        sft = [to_sft(t, route, args.few_shot) for t in turns for route in ("fast", "full")]
        write_jsonl(args.out / f"sft_{split}.jsonl", sft)

        kinds = Counter(t["full_target"]["operation_log_entry"]["kind"] for t in turns)
        sits = Counter(t["fast_target"]["situation"]["type"] for t in turns)
        summary[split] = {
            "scenarios": len(scen_list), "turns": len(turns), "sft_samples": len(sft),
            "kinds": dict(kinds), "situations": dict(sorted(sits.items())),
        }

    total_turns = sum(s["turns"] for s in summary.values())
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n총 발언(턴) {total_turns}건 / SFT 샘플 {sum(s['sft_samples'] for s in summary.values())}건")
    print(f"기획서 3-나 1단계 '학습 데이터 500건 이상': "
          f"{'충족' if total_turns >= 500 else '미달'} ({total_turns}건)")
    print(f"출력: {args.out}")


if __name__ == "__main__":
    main()

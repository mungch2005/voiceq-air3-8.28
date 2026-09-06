"""시연 시나리오 — 대본 읽기, 녹음 파일 찾기, '구운' LLM 결과의 저장·복원.

발표장에서 무료 모델이 느려지거나 429를 뱉으면 시연이 그 자리에서 멈춘다. 그래서
판단 결과를 파일로 구워 두고(save_baked) 발표에서는 그것을 재생한다(load_baked).
재생 경로는 실시간과 완전히 같은 함수(context_memory.apply_fast_result /
apply_full_result)를 타므로, 구운 것과 실시간의 화면 결과가 갈라지지 않는다.

구운 파일은 대본이 바뀌면 못 쓴다 — 발언은 새 대본인데 판단은 옛 대본 것이 되기
때문이다. matches_script()로 발언 목록을 대조해서 걸러낸다.

대본 파일은 두 가지 형태를 모두 읽는다.
  · 리스트          — [{speaker, utterance}, ...]  (초기 scenario1.json)
  · 이름 붙은 묶음  — {name, source, turns: [{speaker, utterance, audio}, ...]}
녹음이 있는 턴은 audio에 data/sample_dialogues/audio/ 아래 파일 이름이 들어 있다.
"""

from __future__ import annotations

import json
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "sample_dialogues"
AUDIO_DIR = DATA_DIR / "audio"

# 사이드바에 이 순서로 나온다. 파일을 찾아 훑지 않고 명시하는 이유는, 대본이 아닌
# JSON(굽기 파일 등)이 같은 폴더에 있고 순서도 발표 흐름에 맞춰 정해야 하기 때문이다.
SCENARIO_IDS = ["drone", "intrusion", "scenario1"]


def _path(scenario_id: str) -> Path:
    return DATA_DIR / f"{scenario_id}.json"


def _baked_path(scenario_id: str) -> Path:
    return DATA_DIR / f"{scenario_id}.baked.json"


def _load(scenario_id: str) -> dict:
    raw = json.loads(_path(scenario_id).read_text(encoding="utf-8"))
    if isinstance(raw, list):  # 이름 없는 옛 형태
        return {"name": scenario_id, "turns": raw}
    return raw


def available() -> list[dict]:
    """실제로 파일이 있는 시나리오들. [{id, name, turns, has_audio, has_bake}]."""
    out = []
    for scenario_id in SCENARIO_IDS:
        if not _path(scenario_id).exists():
            continue
        data = _load(scenario_id)
        turns = data.get("turns", [])
        out.append(
            {
                "id": scenario_id,
                "name": data.get("name", scenario_id),
                "turns": len(turns),
                "has_audio": any(audio_path(scenario_id, i) for i in range(len(turns))),
                "has_bake": matches_script(load_baked(scenario_id), scenario_id),
            }
        )
    return out


def script(scenario_id: str) -> list[dict]:
    """대본 — [{speaker, utterance, audio?}, ...]."""
    return _load(scenario_id).get("turns", [])


def name_of(scenario_id: str) -> str:
    return _load(scenario_id).get("name", scenario_id)


def audio_path(scenario_id: str, index: int) -> Path | None:
    """그 턴의 녹음 파일. 대본에 이름이 없거나 파일이 없으면 None."""
    turns = script(scenario_id)
    if not 0 <= index < len(turns):
        return None
    filename = turns[index].get("audio")
    if not filename:
        return None
    path = AUDIO_DIR / filename
    return path if path.exists() else None


def load_baked(scenario_id: str) -> dict | None:
    """구운 결과. 없거나 읽을 수 없으면 None (실시간으로 돌리면 되므로 예외로 막지 않는다)."""
    path = _baked_path(scenario_id)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def matches_script(baked: dict | None, scenario_id: str) -> bool:
    """구운 결과가 그 대본과 같은 발언들인지. 다르면 재생하면 안 된다."""
    if not baked:
        return False
    baked_turns = baked.get("turns") or []
    current = script(scenario_id)
    if len(baked_turns) != len(current):
        return False
    return all(
        b.get("speaker") == c.get("speaker") and b.get("utterance") == c.get("utterance")
        for b, c in zip(baked_turns, current)
    )


def save_baked(turns: list[dict], model: str, baked_at: str, scenario_id: str) -> Path:
    """굽기 결과를 저장한다. turns는 speaker·utterance·fast·full·display_latency를 담는다."""
    path = _baked_path(scenario_id)
    path.write_text(
        json.dumps(
            {
                "note": (
                    "리허설 때 실제 LLM으로 한 번 돌려 저장한 판단 결과. 발표장 네트워크"
                    " 사고에 대비한 재생용이며, demo.py의 '프리베이크' 재생 소스가 쓴다."
                ),
                "model": model,
                "baked_at": baked_at,
                "turns": turns,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    return path


def describe(baked: dict | None, scenario_id: str) -> str:
    """사이드바에 보여줄 한 줄 상태."""
    if not baked:
        return "구운 결과 없음 — 실시간으로만 재생됩니다."
    if not matches_script(baked, scenario_id):
        return "구운 결과가 지금 대본과 다릅니다 — 다시 구워야 합니다."
    stamp = baked.get("baked_at") or ""
    return f"{baked.get('model', '?')}" + (f" · {stamp}" if stamp else "")


def audio_seconds(scenario_id: str, index: int) -> float | None:
    """그 턴 녹음의 길이(초). 녹음이 없거나 못 읽으면 None.

    재생 박자를 녹음 길이에 맞추려고 쓴다. 고정 초로 두면 긴 발언이 잘리고 다음
    발언이 그 위에 겹쳐 재생된다. m4a 컨테이너의 moov/mvhd 한 상자만 읽으면 되므로
    외부 의존성(ffprobe·mutagen) 없이 표준 라이브러리로 끝낸다.
    """
    path = audio_path(scenario_id, index)
    if path is None:
        return None
    try:
        buf = path.read_bytes()
    except OSError:
        return None

    def walk(start: int, end: int):
        i = start
        while i + 8 <= end:
            size = int.from_bytes(buf[i : i + 4], "big")
            box_type = buf[i + 4 : i + 8]
            body = i + 8
            if size == 1:  # 64비트 길이는 타입 뒤에 이어진다
                size = int.from_bytes(buf[i + 8 : i + 16], "big")
                body = i + 16
            elif size == 0:  # 마지막 상자는 끝까지
                size = end - i
            if size < 8:
                return
            yield box_type, body, i + size
            i += size

    for box_type, body, stop in walk(0, len(buf)):
        if box_type != b"moov":
            continue
        for inner_type, inner_body, _ in walk(body, stop):
            if inner_type != b"mvhd":
                continue
            # mvhd는 버전 바이트 + 플래그 3바이트 뒤에 생성/수정 시각이 오고, 그다음이
            # timescale과 duration이다. 버전 1은 시각이 8바이트씩이라 그만큼 밀린다.
            offset = inner_body + (20 if buf[inner_body : inner_body + 1] == b"\x01" else 12)
            width = 8 if buf[inner_body : inner_body + 1] == b"\x01" else 4
            scale = int.from_bytes(buf[offset : offset + 4], "big")
            ticks = int.from_bytes(buf[offset + 4 : offset + 4 + width], "big")
            return ticks / scale if scale else None
    return None

"""설치 환경 차이를 흡수하는 얇은 호환 계층.

학습·평가 스크립트는 Kaggle / Colab / 부대 내 서버 등 우리가 통제하지 못하는
환경에서 돌아간다. 라이브러리 버전이 어긋나 스크립트가 죽는 경우가 반복돼서,
그런 것들을 여기 모아 둔다. 정상 환경에서는 전부 아무 일도 하지 않는다.
"""

from __future__ import annotations

import importlib
import shutil

# PEFT가 torchao 어댑터 디스패처를 들고 있는 모듈들. 참조가 여러 곳에 복사되므로
# (from ... import is_torchao_available) 모듈 속성을 각각 갈아끼워야 한다.
_TORCHAO_HOLDERS = (
    "peft.import_utils",
    "peft.tuners.lora.torchao",
    "peft.tuners.lora.model",
)


def patch_peft_torchao_check() -> list[str]:
    """PEFT의 torchao 버전 검사가 예외를 던지는 것을 False 반환으로 바꾼다.

    PEFT는 어댑터를 끼울 때 디스패처 목록을 순서대로 훑는데, 베이스가 4bit가 아니면
    (병합처럼 fp16으로 올릴 때) torchao 디스패처를 먼저 시도한다. 그런데 PEFT의
    is_torchao_available()은 torchao가 요구 버전보다 낮게 깔려 있으면 False를
    돌려주는 대신 ImportError를 던진다. Kaggle 이미지에 torchao 0.10.0이 미리 깔려
    있고 PEFT는 0.16 초과를 요구해서, 우리가 쓰지도 않는 torchao 때문에 어댑터 주입이
    통째로 실패했다.

    우리는 torchao 경로를 전혀 쓰지 않으므로 "없는 것으로 친다"가 올바른 동작이다.
    torchao가 아예 없거나 버전이 충분한 정상 환경에서는 원래 함수를 그대로 호출하므로
    동작이 달라지지 않는다.

    반환값은 실제로 갈아끼운 모듈 이름 목록(진단용).
    """
    try:
        import_utils = importlib.import_module("peft.import_utils")
    except ImportError:
        return []

    original = getattr(import_utils, "is_torchao_available", None)
    if original is None or getattr(original, "_voicecue_patched", False):
        return []

    def is_torchao_available_safe(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        except ImportError as exc:
            if not getattr(is_torchao_available_safe, "_warned", False):
                print(f"[compat] torchao 버전 검사를 건너뜁니다 — 이 코드는 torchao를 "
                      f"쓰지 않습니다. ({exc})")
                is_torchao_available_safe._warned = True
            return False

    is_torchao_available_safe._voicecue_patched = True

    patched = []
    for name in _TORCHAO_HOLDERS:
        try:
            module = importlib.import_module(name)
        except ImportError:
            continue
        if getattr(module, "is_torchao_available", None) is not None:
            module.is_torchao_available = is_torchao_available_safe
            patched.append(name)
    return patched


def free_disk_gb(path: str) -> float:
    """해당 경로가 속한 디스크의 여유 공간(GB)."""
    return shutil.disk_usage(path).free / (1024 ** 3)

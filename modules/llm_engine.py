"""OpenRouter 무료 모델 호출 — 표출(FAST) / 기록(FULL) 두 경로를 병렬 실행.

OpenRouter는 OpenAI 호환 Chat Completions API를 제공하므로 `openai` SDK를
base_url만 바꿔서 그대로 사용한다. 무료(:free) 모델은 provider별로
strict tool-calling 지원이 들쭉날쭉하므로, tool 강제 대신 "순수 JSON만
출력하라"는 프롬프트 지시 + 코드펜스 제거 + json.loads 재시도 방식으로
JSON을 강제한다.

지연시간 설계 (핵심):
    화면 표출에 필요한 건 situation(상황 유형 + CCTV 방향) 하나뿐이다. 상황판·
    일지·요약까지 한 번에 생성하면 출력 토큰이 3~4배로 늘어 화면이 그만큼 늦게 뜬다.
    두 호출을 스레드로 동시에 던지고, 표출 경로의 지연시간을 따로 측정한다.
    순차 실행이면 두 지연이 더해지지만 병렬이면 느린 쪽 하나만큼만 걸린다.

    st.secrets / st.session_state는 메인 스레드에서 미리 읽어 인자로 넘긴다.
    워커 스레드에서는 Streamlit 컨텍스트에 접근하지 않는다.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import openai
import streamlit as st

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# ---------------------------------------------------------------------
# 공급자 전환
# ---------------------------------------------------------------------
# 기획서의 최종 목표는 폐쇄망 On-Premise sLLM 구동이다. OpenAI·OpenRouter는
# 프로토타입 시연용 대역일 뿐이므로, 나중에 부대 내 서버로 옮길 때 코드가 아니라
# 설정만 바뀌어야 한다. 세 공급자 모두 OpenAI 호환 Chat Completions API를
# 제공하므로 base_url과 키만 갈아끼우면 된다.
#
# 로컬 전환 예시:
#   Ollama  : ollama serve            -> http://localhost:11434/v1
#   vLLM    : vllm serve <model>      -> http://localhost:8000/v1
#   LM Studio                          -> http://localhost:1234/v1
PROVIDERS = {
    "openrouter": {
        "label": "OpenRouter (무료 티어)",
        "base_url": OPENROUTER_BASE_URL,
        "secret_key": "OPENROUTER_API_KEY",
        "needs_key": True,
    },
    "openai": {
        "label": "OpenAI API",
        "base_url": "https://api.openai.com/v1",
        "secret_key": "OPENAI_API_KEY",
        "needs_key": True,
    },
    "local": {
        "label": "로컬 서버 (Ollama / vLLM) — 폐쇄망 목표 구성",
        "base_url": "http://localhost:11434/v1",
        "secret_key": "LOCAL_API_KEY",
        "needs_key": False,
    },
}

# 2026-08-16 실측 기준 기본값. FAST 경로 평균 2.25초로 후보 중 가장 빠르고 JSON도 안정적이다.
# 무료 목록은 수시로 바뀌므로 https://openrouter.ai/models?max_price=0 에서 확인 후 교체하세요.
DEFAULT_MODEL = "poolside/laguna-xs-2.1:free"

# 하위 호환 — 기존 secrets.toml의 QWEN_MODEL 키를 쓰던 설정을 위해 유지.
DEFAULT_QWEN_MODEL = DEFAULT_MODEL

# 시연 당일 응답이 느리면 사이드바에서 즉시 바꿔볼 수 있도록. 아래는 실측 결과다.
#   laguna-xs   FAST 2.25초 / 위치정확도 2-3      ← 기본값
#   laguna-s    FAST 7.62초 / 위치정확도 3-3, 다만 사태 ID를 "사건N"으로 이탈하는 경우 있음
#   gemma-4-26b FAST 27.8초 / 위치정확도 3-3, 정확하지만 시연에는 너무 느림
#   gpt-oss-20b 추론형이라 reasoning에 토큰을 다 쓰고 답이 비어버린다. effort=low 강제 후에도 33초.
MODEL_CANDIDATES = [
    "poolside/laguna-xs-2.1:free",
    "poolside/laguna-s-2.1:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3-super-120b-a12b:free",
    "openai/gpt-oss-20b:free",
]

# 공급자별 기본 모델과 후보.
# 폐쇄망 목표(4bit 양자화 2~4GB, GPU 10~12GB)를 고려해 작은 모델을 기본값으로 둔다.
# 거대 모델로 시연해 놓고 온프레미스에서 된다고 주장하면 심사에서 반박당한다.
# 아래 목록은 참고용이며, 실제 사용 가능한 모델은 사이드바의 "모델 목록 조회"로 확인할 것.
PROVIDER_MODELS = {
    "openrouter": (DEFAULT_MODEL, MODEL_CANDIDATES),
    "openai": (
        "gpt-4.1-mini",
        ["gpt-4.1-mini", "gpt-4o-mini", "gpt-4.1", "gpt-4o"],
    ),
    "local": (
        "qwen2.5:7b-instruct",
        ["qwen2.5:7b-instruct", "qwen2.5:14b-instruct", "gemma2:9b", "llama3.1:8b"],
    ),
}


def default_model_for(provider_id: str) -> str:
    return PROVIDER_MODELS.get(provider_id, PROVIDER_MODELS["openrouter"])[0]


def candidates_for(provider_id: str) -> list[str]:
    return list(PROVIDER_MODELS.get(provider_id, PROVIDER_MODELS["openrouter"])[1])

# 추론형 모델은 내부 추론에 출력 토큰을 전부 소진해 content가 비어버린다.
# OpenRouter는 이 공통 파라미터로 추론 강도를 낮출 수 있다.
# 단 이건 OpenRouter 전용이다. OpenAI에 보내면 400 "Unrecognized request argument"가 난다.
REASONING_PARAM = {"reasoning": {"effort": "low"}}


def provider_extra_body(provider_id: str) -> dict | None:
    """공급자별 비표준 파라미터. 지원하지 않는 곳에 보내면 400이 난다."""
    return REASONING_PARAM if provider_id == "openrouter" else None

FAST_KEYS = ("situation",)
FULL_KEYS = ("context_memory", "situation_board", "operation_log_entry")

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


@dataclass
class LLMResult:
    data: dict
    latency_seconds: float
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class TurnResult:
    """한 발언에 대한 두 경로의 결과. 한쪽이 실패해도 나머지는 살린다."""

    fast: LLMResult | None = None
    full: LLMResult | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def display_latency(self) -> float:
        """발언 종료 → 화면 구성 결정까지. 심사 지표로 제시할 값."""
        return self.fast.latency_seconds if self.fast else 0.0

    @property
    def total_latency(self) -> float:
        return max(
            self.fast.latency_seconds if self.fast else 0.0,
            self.full.latency_seconds if self.full else 0.0,
        )


def configured_provider() -> str:
    """세션 기본 공급자를 결정한다.

    LLM_PROVIDER를 secrets에 명시했으면 그걸 따른다. 명시하지 않았으면
    실제로 키가 들어있는 공급자를 자동으로 고른다 — OPENROUTER_API_KEY만
    있으면 openrouter, OPENAI_API_KEY만 있으면 openai. 이게 없으면
    새로고침마다 기본값이 openrouter로 되돌아가 버려서, OpenAI 키만 넣어둔
    사람은 매번 사이드바에서 공급자를 수동으로 바꿔야 하는 문제가 있었다.
    """
    value = str(st.secrets.get("LLM_PROVIDER", "") or "").strip().lower()
    if value in PROVIDERS:
        return value

    if str(st.secrets.get("OPENROUTER_API_KEY", "") or "").strip():
        return "openrouter"
    if str(st.secrets.get("OPENAI_API_KEY", "") or "").strip():
        return "openai"
    return "openrouter"


def _validate_api_key(api_key: str, provider: dict) -> None:
    """키를 HTTP 헤더에 싣기 전에 검사한다.

    비ASCII 문자가 섞이면 httpx가 헤더 인코딩 단계에서 UnicodeEncodeError로 죽는다.
    스택 트레이스만 보고는 원인을 알 수 없으므로, 무엇이 잘못됐는지 짚어서 알려준다.
    실제로 안내 문구("...붙여넣으세요")의 끝 글자가 남는 사고가 있었다.
    키 값 자체는 절대 메시지에 담지 않는다.
    """
    where = f".streamlit/secrets.toml 의 {provider['secret_key']}"

    if not api_key or "여기에" in api_key:
        raise RuntimeError(f"{where} 가 아직 설정되지 않았습니다. {provider['label']} 키를 등록하세요.")

    bad = [(i, c) for i, c in enumerate(api_key) if ord(c) > 127]
    if bad:
        positions = ", ".join(f"{i + 1}번째('{c}')" for i, c in bad[:5])
        raise RuntimeError(
            f"{where} 에 한글 등 ASCII가 아닌 문자가 {len(bad)}개 섞여 있습니다 "
            f"[{positions}]. 전체 {len(api_key)}자 중 앞 {len(api_key) - len(bad)}자만 정상입니다. "
            f"안내 문구가 지워지지 않고 남은 경우가 대부분이니, 해당 글자를 삭제하고 저장하세요."
        )

    if any(c.isspace() for c in api_key):
        raise RuntimeError(f"{where} 에 공백이나 줄바꿈이 섞여 있습니다. 제거하고 저장하세요.")


def get_runtime() -> tuple[Callable[[], openai.OpenAI], str, dict | None]:
    """메인 스레드에서만 호출할 것 — st.secrets / st.session_state를 읽는다.

    클라이언트를 미리 만들어 반환하지 않고 '클라이언트를 새로 만드는 함수'를 반환한다.
    FAST/FULL 두 경로가 스레드로 동시에 호출되는데, httpx 커넥션 풀을 공유하는 클라이언트
    하나를 두 스레드가 동시에 쓰면 드물게 한쪽 요청에 Authorization 헤더가 누락되는
    레이스 컨디션이 발생한다(OpenRouter가 401 "Missing Authentication header"로 응답).
    스레드마다 별도 클라이언트를 만들면 이 공유 상태 자체가 사라진다.

    반환: (클라이언트 팩토리, 모델 ID, 공급자별 추가 파라미터)
    """
    provider_id = st.session_state.get("provider") or configured_provider()
    provider = PROVIDERS.get(provider_id, PROVIDERS["openrouter"])

    api_key = str(st.secrets.get(provider["secret_key"], "")).strip()
    if provider["needs_key"]:
        _validate_api_key(api_key, provider)

    base_url = st.secrets.get("LOCAL_BASE_URL", provider["base_url"]) if (
        provider_id == "local"
    ) else provider["base_url"]

    def make_client() -> openai.OpenAI:
        return openai.OpenAI(
            base_url=base_url,
            api_key=api_key or "not-needed",  # 로컬 서버는 키를 요구하지 않는다.
            timeout=40.0,
        )

    model = st.session_state.get("selected_model") or st.secrets.get(
        "QWEN_MODEL", DEFAULT_MODEL
    )
    return make_client, model, provider_extra_body(provider_id)


def list_models(provider_id: str) -> list[str]:
    """공급자가 실제로 제공하는 모델 ID를 조회한다.

    모델 목록은 수시로 바뀌므로 코드에 박아 두지 않고 런타임에 물어본다.
    OpenRouter 무료 모델이 유료로 전환되거나 로컬 서버에 어떤 모델이 올라와
    있는지 확인할 때도 이 함수를 쓴다.
    """
    provider = PROVIDERS.get(provider_id, PROVIDERS["openrouter"])
    api_key = st.secrets.get(provider["secret_key"], "")
    base_url = st.secrets.get("LOCAL_BASE_URL", provider["base_url"]) if (
        provider_id == "local"
    ) else provider["base_url"]

    client = openai.OpenAI(base_url=base_url, api_key=api_key or "not-needed", timeout=20.0)
    models = [m.id for m in client.models.list().data]

    if provider_id == "openrouter":
        models = [m for m in models if m.endswith(":free")]
    return sorted(models)


def _extract_json(raw_text: str, required_keys: tuple[str, ...]) -> dict:
    text = _CODE_FENCE_RE.sub("", raw_text.strip()).strip()

    # 모델이 JSON 앞뒤로 군더더기 텍스트를 붙이는 경우, 첫 '{'부터 마지막 '}'까지만 취한다.
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("응답에서 JSON 객체를 찾을 수 없습니다.")

    parsed = json.loads(text[start : end + 1])
    missing = [k for k in required_keys if k not in parsed]
    if missing:
        raise ValueError(f"필수 키 누락: {missing}")
    return parsed


def call_llm(
    client_factory: Callable[[], openai.OpenAI],
    model: str,
    system_prompt: str,
    few_shot_messages: list[dict],
    user_turn: str,
    required_keys: tuple[str, ...],
    max_tokens: int,
    extra_body: dict | None = None,
    max_retries: int = 1,
) -> LLMResult:
    """구조화된 JSON 1건을 받아온다. 워커 스레드에서 실행되므로 st.* 접근 금지.

    시도(재시도 포함)마다 클라이언트를 새로 만든다. 다른 스레드와 커넥션을
    공유하지 않기 위함이며, 이전 시도의 커넥션 상태를 다음 재시도로 들고
    가지 않기 위함이기도 하다.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        *few_shot_messages,
        {"role": "user", "content": user_turn},
    ]

    last_error: Exception | None = None
    for _ in range(max_retries + 1):
        client = client_factory()
        start = time.monotonic()
        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=0.2,
                response_format={"type": "json_object"},
                extra_body=extra_body,
                extra_headers={
                    "HTTP-Referer": "https://voice-cue.local",
                    "X-Title": "VOICE-CUE",
                },
            )
        except openai.RateLimitError as e:
            # 무료 티어는 하루 50회다. 재시도하면 한도만 더 깎아먹으므로 즉시 중단한다.
            raise RuntimeError(
                "OpenRouter 무료 한도(하루 50회)를 모두 소진했습니다. "
                "한국시간 오전 9시에 초기화되며, openrouter.ai에서 크레딧 10달러를 충전하면 "
                f"하루 1000회로 늘어납니다. (원문: {str(e)[:120]})"
            ) from e
        except (openai.APIStatusError, openai.APIConnectionError, openai.APITimeoutError) as e:
            last_error = e
            continue

        latency = time.monotonic() - start

        choice = response.choices[0] if response.choices else None
        message = choice.message if choice else None
        content = message.content if message else None
        if not content:
            # 추론형 모델에서 흔한 실패다. 내부 추론에 토큰을 다 쓰고 답변을 못 낸 것이므로
            # 재시도해도 같은 결과다. 어떤 모델로 바꿔야 하는지 바로 알려준다.
            if getattr(message, "reasoning", None):
                raise RuntimeError(
                    f"'{model}'은 추론형 모델이라 답변 대신 내부 추론만 반환했습니다. "
                    f"사이드바에서 {DEFAULT_MODEL} 등 비추론 모델로 바꾸세요."
                )
            last_error = ValueError("모델이 빈 응답을 반환했습니다.")
            continue

        try:
            parsed = _extract_json(content, required_keys)
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
            continue

        usage = response.usage
        return LLMResult(
            data=parsed,
            latency_seconds=latency,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        )

    if isinstance(last_error, openai.AuthenticationError):
        raise RuntimeError(
            f"인증 실패(401)로 {max_retries + 1}회 모두 실패했습니다. API 키 자체가 "
            f"잘못됐거나(secrets.toml 확인) 만료됐을 가능성이 큽니다. (원문: {str(last_error)[:150]})"
        ) from last_error
    raise RuntimeError(str(last_error))


def analyze_turn(
    client_factory: Callable[[], openai.OpenAI],
    model: str,
    fast_system: str,
    fast_few_shot: list[dict],
    fast_turn: str,
    full_system: str,
    full_few_shot: list[dict],
    full_turn: str,
    extra_body: dict | None = None,
) -> TurnResult:
    """표출 경로와 기록 경로를 동시에 던진다. 두 경로가 클라이언트를 공유하지 않도록
    각자 client_factory를 통해 자기 커넥션을 새로 만든다."""
    result = TurnResult()

    with ThreadPoolExecutor(max_workers=2) as pool:
        fast_future = pool.submit(
            call_llm, client_factory, model, fast_system, fast_few_shot, fast_turn,
            FAST_KEYS, 600, extra_body,
        )
        full_future = pool.submit(
            call_llm, client_factory, model, full_system, full_few_shot, full_turn,
            FULL_KEYS, 800, extra_body,
        )

        try:
            result.fast = fast_future.result()
        except RuntimeError as e:
            result.errors.append(f"표출 경로 실패: {e}")

        try:
            result.full = full_future.result()
        except RuntimeError as e:
            result.errors.append(f"기록 경로 실패: {e}")

    return result

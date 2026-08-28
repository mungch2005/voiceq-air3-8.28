"""OpenAI Whisper API 기반 음성 → 텍스트 변환.

LLM 판단 경로(modules/llm_engine.py)는 공급자를 OpenRouter/OpenAI/로컬로 전환할 수
있지만, STT는 항상 OpenAI Whisper API를 쓴다. OpenRouter는 채팅 완성 API만 제공하고
오디오 전사(audio transcription) 엔드포인트는 없기 때문이다. 그래서 이 모듈은
llm_engine의 공급자 선택과 무관하게 secrets의 OPENAI_API_KEY를 직접 읽는다.
"""

from __future__ import annotations

import openai
import streamlit as st

WHISPER_MODEL = "whisper-1"


def is_configured() -> bool:
    key = str(st.secrets.get("OPENAI_API_KEY", "") or "").strip()
    return bool(key) and "여기에" not in key


def transcribe(audio_bytes: bytes, filename: str = "utterance.wav") -> str:
    """오디오 바이트를 한국어 텍스트로 변환한다. 실패 시 RuntimeError."""
    api_key = str(st.secrets.get("OPENAI_API_KEY", "") or "").strip()
    if not api_key or "여기에" in api_key:
        raise RuntimeError(
            ".streamlit/secrets.toml 의 OPENAI_API_KEY 가 설정되지 않았습니다. "
            "음성 입력은 OpenRouter 키와 별개로 OpenAI Whisper API 키가 필요합니다."
        )

    client = openai.OpenAI(api_key=api_key, timeout=60.0)
    try:
        result = client.audio.transcriptions.create(
            model=WHISPER_MODEL,
            file=(filename, audio_bytes),
            language="ko",
        )
    except openai.AuthenticationError as e:
        raise RuntimeError(
            f"인증 실패(401)로 음성 변환이 거부됐습니다. OPENAI_API_KEY를 확인하세요. "
            f"(원문: {str(e)[:150]})"
        ) from e
    except (openai.APIStatusError, openai.APIConnectionError, openai.APITimeoutError) as e:
        raise RuntimeError(f"Whisper 호출 실패: {str(e)[:200]}") from e

    text = (result.text or "").strip()
    if not text:
        raise RuntimeError("음성에서 텍스트를 인식하지 못했습니다. 다시 녹음해 보세요.")
    return text

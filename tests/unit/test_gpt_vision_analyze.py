"""
GPTVisionService.analyze_food_with_detection 서비스 단위 계약 테스트.

TODO #4 Step 4에서 이 메서드만 LangChain(ChatOpenAI/HumanMessage)에서
OpenAI SDK(AsyncOpenAI.chat.completions.create)로 전환된다.

가장 놓치기 쉬운 변화: 멀티모달 메시지 shape
- LangChain HumanMessage content의 image_url은 **문자열**
- OpenAI SDK user message content의 image_url은 **{"url": ...} dict**
교체 시 이 포맷을 놓치면 API가 조용히 실패할 수 있어서 shape 테스트 필수.

Mock guard values 원칙:
- mock이 반환하는 food_name과 YOLO 감지 요약의 food_name을 다르게 설정.
- mock이 안 타면 파서가 "분석 실패" 또는 YOLO 이름을 그대로 흘려보낼 수 있어 판별 가능.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.gpt_vision_service import GPTVisionService

# Step 2에서 학습한 규칙: 공유 engine 풀이 session loop에 묶이도록 강제.
pytestmark = pytest.mark.asyncio(loop_scope="session")


# 파서가 파싱 가능한 최소 GPT 응답 — 후보 1개 + 주요재료 + 신뢰도.
# 의도적으로 "테스트음식A" 라는 독특한 이름으로 고정 (guard value).
_MOCK_GPT_RESPONSE = """[후보 1]
음식명: 테스트음식A
신뢰도: 95%
설명: mock 응답 설명
주요재료 1: 재료A
주요재료 2: 재료B
"""

# YOLO 감지 결과의 food_name은 "피자" — mock 응답의 food_name과 다름.
# 만약 mock이 안 타고 YOLO 정보가 어딘가로 새어서 반환되면 구별 가능.
_YOLO_RESULT = {
    "summary": "피자 1개 감지됨",
    "detected_objects": [
        {"class_name": "pizza", "confidence": 0.92}
    ],
    "total_objects": 1,
}

# 아주 작은 JPEG-like 바이트 (파일 형식 검증 없음, _image_to_base64만 통과하면 됨)
_FAKE_IMAGE_BYTES = b"\xff\xd8\xff\xe0fake_jpeg_payload_for_tests"


def _make_openai_mock(content: str) -> MagicMock:
    """AsyncOpenAI.chat.completions.create가 `content`를 돌려주도록 형태를 맞춘 mock."""
    message = MagicMock()
    message.content = content
    choice = MagicMock()
    choice.message = message
    response = MagicMock()
    response.choices = [choice]

    client = MagicMock()
    client.chat.completions.create = AsyncMock(return_value=response)
    return client


def _service_with_mock(mock_client: MagicMock | None) -> GPTVisionService:
    """OPENAI_API_KEY 체크를 우회하고 client만 주입한 서비스 인스턴스."""
    service = GPTVisionService.__new__(GPTVisionService)
    service.client = mock_client
    return service


async def test_analyze_food_with_detection_returns_parsed_response():
    """Happy path: LLM mock 응답이 _parse_gpt_response를 거쳐 dict로 반환된다.

    guard: 반환 food_name='테스트음식A'는 YOLO summary의 '피자'와 다름.
    mock이 안 타면 파서가 '분석 실패' 기본값을 반환하거나 전혀 다른 값 → 즉시 실패.
    """
    mock_client = _make_openai_mock(_MOCK_GPT_RESPONSE)
    service = _service_with_mock(mock_client)

    result = await service.analyze_food_with_detection(_FAKE_IMAGE_BYTES, _YOLO_RESULT)

    assert result["food_name"] == "테스트음식A"
    assert result["ingredients"] == ["재료A", "재료B"]
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["confidence"] == 0.95
    mock_client.chat.completions.create.assert_awaited_once()


async def test_analyze_food_with_detection_passes_yolo_summary_to_prompt():
    """YOLO 감지 요약이 GPT 프롬프트의 text 필드에 포함되어야 한다."""
    mock_client = _make_openai_mock(_MOCK_GPT_RESPONSE)
    service = _service_with_mock(mock_client)

    await service.analyze_food_with_detection(_FAKE_IMAGE_BYTES, _YOLO_RESULT)

    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    messages = call_kwargs["messages"]
    user_content = messages[-1]["content"]
    text_parts = [item["text"] for item in user_content if item.get("type") == "text"]
    combined_text = "\n".join(text_parts)
    assert "피자 1개 감지됨" in combined_text


async def test_analyze_food_with_detection_sends_image_url_in_sdk_shape():
    """멀티모달 content의 image_url은 SDK 포맷({"url": ...} dict) 이어야 한다.

    LangChain은 image_url을 문자열로도 허용하지만 OpenAI SDK는 dict 전용.
    교체 시 가장 놓치기 쉬운 포맷 변화를 고정.
    """
    mock_client = _make_openai_mock(_MOCK_GPT_RESPONSE)
    service = _service_with_mock(mock_client)

    await service.analyze_food_with_detection(_FAKE_IMAGE_BYTES, _YOLO_RESULT)

    call_kwargs = mock_client.chat.completions.create.await_args.kwargs
    user_content = call_kwargs["messages"][-1]["content"]
    image_items = [item for item in user_content if item.get("type") == "image_url"]
    assert len(image_items) == 1, "user message에 image_url 항목이 정확히 1개여야 함"

    image_field = image_items[0]["image_url"]
    assert isinstance(image_field, dict), (
        f"SDK 포맷은 dict여야 함 (LangChain식 문자열 X). got: {type(image_field).__name__}"
    )
    assert "url" in image_field
    assert image_field["url"].startswith("data:image/jpeg;base64,")


async def test_analyze_food_with_detection_raises_when_client_not_initialized():
    """self.client is None이면 RuntimeError — API key 미설정 상태 보호."""
    service = _service_with_mock(None)

    with pytest.raises(RuntimeError, match="OpenAI 클라이언트"):
        await service.analyze_food_with_detection(_FAKE_IMAGE_BYTES, _YOLO_RESULT)
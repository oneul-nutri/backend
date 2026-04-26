"""
NutrientEstimatorService.estimate_nutrients 서비스 단위 계약 테스트.

TODO #4 (LangChain → OpenAI SDK) 교체 시 내부 구현이 바뀌어도
반환 스키마와 경계 동작(JSON 파싱 실패, 칼로리 자동 계산, 기본값 병합)은
유지되어야 한다. 라우트 레벨 테스트 대신 서비스 단위로 계약을 고정한다.

Mock guard values 원칙:
- happy path의 mock 응답은 auto-calc 산식(P*4 + C*4 + F*9) 결과와 다른 숫자를
  사용해 "mock은 안 탔는데 우연히 값이 같아서 녹색"인 상황을 배제한다.
"""
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.llm_nutrient_estimator import NutrientEstimatorService

# pytest.ini의 asyncio_default_fixture_loop_scope=session 과 맞추기 위해
# 테스트도 session loop에서 실행되도록 강제한다. 불일치 시 공유 engine 풀의
# 커넥션이 다른 loop에서 소비되어 후속 DB 테스트에서 asyncmy "Command Out of Sync"
# 에러가 발생한다.
pytestmark = pytest.mark.asyncio(loop_scope="session")


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


def _service_with_mock(mock_client: MagicMock) -> NutrientEstimatorService:
    """OPENAI_API_KEY 체크를 우회하고 client만 주입한 서비스 인스턴스."""
    service = NutrientEstimatorService.__new__(NutrientEstimatorService)
    service.client = mock_client
    return service


async def test_estimate_nutrients_returns_mock_values_when_llm_responds():
    """LLM이 유효한 JSON을 반환하면 그 값을 그대로 반영한다.

    guard: mock calories=275 는 4-4-9 auto-calc 결과(12.5*4+35*4+6*9=244)와 다름.
    mock이 작동하지 않고 auto-calc 경로로 떨어지면 244 → 275 와 불일치로 실패.
    """
    mock_payload = {
        "protein": 12.5,
        "carbs": 35.0,
        "fat": 6.0,
        "fiber": 2.0,
        "sodium": 450,
        "calcium": 80,
        "iron": 1.5,
        "vitamin_a": 50,
        "vitamin_c": 8,
        "potassium": 200,
        "magnesium": 40,
        "saturated_fat": 2.0,
        "cholesterol": 30,
        "trans_fat": 0,
        "added_sugar": 3,
        "calories": 275,
        "total_weight_g": 300,
        "food_class1": "샐러드류",
        "food_class2": "닭가슴살",
    }
    service = _service_with_mock(_make_openai_mock(json.dumps(mock_payload)))

    result = await service.estimate_nutrients("닭가슴살 샐러드", ["닭가슴살", "양상추"])

    assert result["calories"] == 275
    assert result["protein"] == 12.5
    assert result["food_class1"] == "샐러드류"
    assert result["food_class2"] == "닭가슴살"


async def test_estimate_nutrients_auto_calculates_calories_when_llm_returns_zero():
    """LLM이 calories=0을 반환하면 4-4-9 공식으로 자동 계산한다.

    10*4 + 30*4 + 5*9 = 40 + 120 + 45 = 205
    """
    mock_payload = {
        "protein": 10.0,
        "carbs": 30.0,
        "fat": 5.0,
        "calories": 0,
    }
    service = _service_with_mock(_make_openai_mock(json.dumps(mock_payload)))

    result = await service.estimate_nutrients("김밥", [])

    assert result["calories"] == 205


async def test_estimate_nutrients_returns_defaults_on_invalid_json():
    """LLM 응답이 JSON 파싱 실패 시 모든 필드가 기본값(0/None)으로 채워진다."""
    service = _service_with_mock(_make_openai_mock("이건 JSON이 아니야"))

    result = await service.estimate_nutrients("불명 음식", [])

    assert result["calories"] == 0
    assert result["protein"] == 0.0
    assert result["food_class1"] == "사용자추가"
    assert result["food_class2"] is None


async def test_estimate_nutrients_response_schema_complete():
    """부분 키만 반환돼도 기본값 병합으로 전체 스키마가 보장된다."""
    mock_payload = {"calories": 100, "protein": 5, "carbs": 20, "fat": 3}
    service = _service_with_mock(_make_openai_mock(json.dumps(mock_payload)))

    result = await service.estimate_nutrients("빵", [])

    required_keys = {
        "protein", "carbs", "fat", "fiber", "sodium", "calcium", "iron",
        "vitamin_a", "vitamin_c", "potassium", "magnesium",
        "saturated_fat", "cholesterol", "trans_fat", "added_sugar",
        "calories", "food_class1", "food_class2",
    }
    assert required_keys.issubset(result.keys())

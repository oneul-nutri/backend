import asyncio
import json
import re
import uuid
from contextlib import suppress
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from openai import AsyncOpenAI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_active_user
from app.api.v1.schemas.chat import ChatMessageRequest, ChatMessageResponse
from app.core.config import get_settings
from app.db.models import ChatHistory, Conversation, User
from app.db.redis_session import get_redis_client
from app.db.session import get_session
from app.services.chat_service import ChatService
from app.services.recipe_recommendation_service import get_recipe_recommendation_service
from app.services.user_context_cache import get_or_build_user_context, refresh_user_context

# 기존 chat.py와 충돌을 피하기 위해 prefix 변경 또는 파일명만 다르게 사용
# 여기서는 라우터 설정은 나중에 메인에서 연결할 것이므로 내용은 chat.py와 유사하게 유지
router = APIRouter(prefix="/chat", tags=["Chat"])

settings = get_settings()


@lru_cache
def get_clarify_llm() -> AsyncOpenAI:
    """Clarify 응답 생성용 OpenAI 클라이언트 팩토리.

    이름은 LangChain 시절 그대로 유지 — 테스트 패치 지점(app.api.v1.routes.chat_v2.get_clarify_llm)
    하위 호환. 모델/temperature/response_format은 호출 시점 인자로 지정.
    """
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured.")
    return AsyncOpenAI(api_key=settings.openai_api_key)


CLARIFY_CONFIRMATION_MESSAGE = "레시피를 추천해드릴까요? 진행을 원하시면 '네' 또는 '응'이라고 답해주세요."

RECIPE_NEGATION_KEYWORDS = [
    "레시피 말고",
    "레시피 필요 없어",
    "레시피 필요없어",
    "아니",
    "아니요",
    "아니오",
    "싫어",
    "됐어",
    "괜찮아",
]


RECIPE_REQUEST_PATTERNS = [
    re.compile(
        r"(레시피|조리법|요리법|만드는\s?법|만드는\s?방법).*(알려|추천|보여|찾아|줄|해줘|부탁|가능|가르쳐)"
    ),
    re.compile(
        r"(알려|추천|보여|찾아|줄|해줘|부탁|가능|가르쳐).*(레시피|조리법|요리법|만드는\s?법|만드는\s?방법)"
    ),
    re.compile(r"(어떻게|방법).*(만들|요리해)"),
    re.compile(r"(레시피|조리법|만드는\s?법).*(추천해줘|알려줘|보여줘|찾아줘)"),
]


def _log_recipe_debug(event: str, extra: Optional[Dict[str, Any]] = None) -> None:
    payload: Dict[str, Any] = {
        "event": event,
        "ts": datetime.utcnow().isoformat(),
    }
    if extra:
        payload.update(extra)
    try:
        serialized = json.dumps(payload, ensure_ascii=False)
    except TypeError:
        serialized = str(payload)
    print(f"🧩 [RecipeConfirm] {serialized}")


_CLARIFY_SYSTEM_TEMPLATE = """너는 친근한 한국어 영양사 챗봇이야.
- 최근 대화 요약: {summary}
- 항상 JSON 객체만 출력해야 해. 필드는 response_id, action_type, message, suggestions, needs_tool_call.
- 사용자가 잡담이나 영양/건강 관련 질문을 하면 자연스럽게 답하고, 필요한 경우 부드럽게 추가 정보를 물어봐.
- 사용자가 “아니”, “싫어” 등 부정 표현을 쓰면 needs_tool_call은 false로 유지하고 대화형 텍스트로 응답해.
- "레시피 추천해줘", "만드는 법 알려줘", "~ 어떻게 만들어?"처럼 명확하게 추천/조리법을 요청할 때만 needs_tool_call을 true로 하고, message에 확인 문구를 넣어.
- needs_tool_call이 true일 때만 message에 "레시피를 추천해드릴까요? 진행을 원하시면 '네' 또는 '응'이라고 답해주세요."를 포함해.
- needs_tool_call이 false일 때는 확인 문구나 과한 추가 질문 없이 자연스러운 대화/질문/정보만 message에 담아.
- 모호한 질문이나 정보 탐색, 부정 표현은 needs_tool_call=false로 유지해.
- suggestions에는 사용자가 바로 클릭해서 보낼 수 있는 짧은 발화 예시 2~3개(예: "매콤한 레시피 추천해줘", "다른 질문 있어")만 넣어. 챗봇이 던지는 질문은 message에만 넣어.
- action_type은 항상 TEXT_ONLY로 고정해.
"""


def _normalize_text_for_intent(text: str) -> str:
    return (text or "").strip().lower()


def _matches_recipe_request(text: str) -> bool:
    normalized = _normalize_text_for_intent(text)
    if not normalized:
        return False
    return any(pattern.search(normalized) for pattern in RECIPE_REQUEST_PATTERNS)


def _evaluate_recipe_intent_flags(user_message: str) -> tuple[bool, bool]:
    normalized = _normalize_text_for_intent(user_message)
    if not normalized:
        return False, False
    has_negation = any(keyword in normalized for keyword in RECIPE_NEGATION_KEYWORDS)
    if has_negation:
        return False, True
    has_recipe_request = _matches_recipe_request(normalized)
    return has_recipe_request, False


async def _generate_clarify_payload(summary: str, user_message: str) -> dict:
    clarify_client = get_clarify_llm()
    system_content = _CLARIFY_SYSTEM_TEMPLATE.format(summary=summary or "이전 대화 없음")
    try:
        response = await clarify_client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.4,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_message},
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
    except Exception:
        payload = {}

    payload.setdefault("response_id", f"clarify-{uuid.uuid4()}")
    payload.setdefault("action_type", "TEXT_ONLY")
    payload.setdefault("message", "사용자님의 요청을 이해했어요. 더 자세히 말씀해주실까요?")
    suggestions = payload.get("suggestions")
    if not isinstance(suggestions, list) or len(suggestions) == 0:
        payload["suggestions"] = ["레시피 추천해줘", "다른 질문 있어"]
    payload.setdefault("needs_tool_call", False)
    return payload


@router.get("/context")
async def refresh_chat_context(
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_session),
):
    """Recommend 탭 진입 시 최신 사용자 컨텍스트를 강제로 갱신."""
    ctx = await refresh_user_context(db, current_user.user_id)
    return {
        "success": True,
        "message": "컨텍스트를 새로고침했습니다.",
        "data": {
            "diseases": ctx.diseases,
            "allergies": ctx.allergies,
            "has_eaten_today": ctx.has_eaten_today,
            "last_refreshed": ctx.last_refreshed.isoformat(),
        },
    }


@router.post("/prewarm")
async def prewarm_chat_agent(
    current_user: User = Depends(get_current_active_user),
) -> dict:
    """Recommend 탭 진입 시 프론트가 호출하는 fire-and-forget no-op stub.

    이전에는 LangChain ReAct 에이전트를 워밍업했으나, agent tool 4개가 모두
    존재하지 않는 서비스 메서드를 호출하는 dead 구현이라 runtime 확률적 실패만
    일으켰다. 프론트는 응답 body를 검사하지 않으므로 2xx만 보장하면 충분하다.
    """
    return {"success": True, "message": "ready"}


@router.post("", response_model=ChatMessageResponse)
async def handle_chat_message(
    request: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_session),
    redis_client = Depends(get_redis_client),
):
    """
    [v2] 개선된 채팅 핸들러
    - LangChain Agent 사용을 최소화하고, 명확한 레시피 요청 시 단축 경로(Shortcut)를 사용
    - 건강 유해성 체크 선행 (quick_analyze_intent)
    """
    chat_service = ChatService(redis_client=redis_client, db_session=db)

    previous_session_id = await chat_service.get_previous_session_id_and_update(
        user_id=current_user.user_id, current_session_id=request.session_id
    )

    if previous_session_id and previous_session_id != request.session_id:
        background_tasks.add_task(
            chat_service.summarize_conversation_if_needed, previous_session_id
        )

    cached_context = await get_or_build_user_context(db, current_user.user_id)
    diseases = cached_context.diseases
    allergies = cached_context.allergies
    has_eaten_today = cached_context.has_eaten_today
    recipe_service = get_recipe_recommendation_service()

    conversation = await db.get(Conversation, request.session_id)
    is_new_conversation = conversation is None

    conversation_summary = ""
    if is_new_conversation:
        summary_stmt = (
            select(Conversation.sum_chat)
            .where(
                Conversation.user_id == current_user.user_id,
                Conversation.sum_chat.isnot(None),
            )
            .order_by(Conversation.last_message_summarized_at.desc())
            .limit(1)
        )
        summary_result = await db.execute(summary_stmt)
        latest_summary = summary_result.scalar_one_or_none()
        if latest_summary:
            conversation_summary = latest_summary
    elif conversation and conversation.sum_chat:
        conversation_summary = conversation.sum_chat

    mode = (request.mode or "clarify").lower()
    ai_response_payload: str
    display_text: str
    needs_tool_call_flag = False

    # ----------------------------------------------------------------------
    # [Step 1] Clarify 모드: 단순 대화 및 의도 파악
    # ----------------------------------------------------------------------
    if mode == "clarify":
        force_tool_call, force_tool_block = _evaluate_recipe_intent_flags(request.message)
        clarify_payload = await _generate_clarify_payload(conversation_summary, request.message)

        if force_tool_block:
            clarify_payload["needs_tool_call"] = False
            message_text = clarify_payload.get("message", "")
            if CLARIFY_CONFIRMATION_MESSAGE in message_text:
                clarify_payload["message"] = "알겠습니다. 다른 요청이 있으면 말씀해주세요!"
        elif force_tool_call:
            clarify_payload["needs_tool_call"] = True
            clarify_payload["message"] = CLARIFY_CONFIRMATION_MESSAGE

        needs_tool_call_flag = bool(clarify_payload.get("needs_tool_call"))
        ai_response_payload = json.dumps(clarify_payload, ensure_ascii=False)
        display_text = clarify_payload.get("message", "")

    # ----------------------------------------------------------------------
    # [Step 2] Execute 모드: 레시피 추천 (단축 경로 적용)
    # ----------------------------------------------------------------------
    else:
        safety_mode = (request.safety_mode or "").lower()
        
        # 1. 건강 유해성 체크 선행 (이미 safety_mode가 정해진 경우는 생략 가능하지만 안전을 위해 체크 권장)
        # 단, safety_mode가 있다는 건 이미 경고를 보고 선택했다는 뜻이므로 체크 건너뜀
        if safety_mode not in ["proceed", "health_first"]:
            _log_recipe_debug("HealthCheckStart", {"session_id": request.session_id})
            
            try:
                quick_analysis = await recipe_service.quick_analyze_intent(
                    user=current_user,
                    intent_text=request.message,
                    diseases=diseases,
                    allergies=allergies,
                    has_eaten_today=has_eaten_today,
                )
                
                disease_conflict = bool(quick_analysis.get("disease_conflict"))
                allergy_conflict = bool(quick_analysis.get("allergy_conflict"))
                
                if disease_conflict or allergy_conflict:
                    # 위험 감지 -> 즉시 경고 리턴 (Agent 실행 X)
                    _log_recipe_debug("HealthConflictDetected", {"disease": disease_conflict, "allergy": allergy_conflict})
                    
                    disease_text = ", ".join(diseases or []) or "없음"
                    allergy_text = ", ".join(allergies or []) or "없음"
                    conflict_lines = []
                    if disease_conflict:
                        conflict_lines.append(f"등록된 질병({disease_text})과 요청한 메뉴가 충돌할 수 있어요.")
                    if allergy_conflict:
                        conflict_lines.append(f"알레르기 목록({allergy_text})에 포함된 재료가 있어요.")
                    
                    combined_warning = quick_analysis.get("health_warning") or "\n".join(conflict_lines)
                    confirm_message = (
                        f"{quick_analysis.get('user_message') or '건강을 고려해볼까요?'}\n\n"
                        f"{combined_warning}\n\n"
                        "건강을 우선해서 레시피를 조정할까요, 아니면 그대로 진행할까요?"
                    )
                    
                    health_payload = {
                        "response_id": f"health-{uuid.uuid4()}",
                        "action_type": "HEALTH_CONFIRMATION",
                        "message": confirm_message,
                        "data": {
                            "health_warning": combined_warning,
                            "user_friendly_message": quick_analysis.get("user_message"),
                        },
                        "suggestions": ["그대로 진행해줘", "건강하게 바꿔줘"],
                    }
                    
                    ai_response_payload = json.dumps(health_payload, ensure_ascii=False)
                    display_text = confirm_message
                    
                    # 여기서 리턴하기 위해 아래 로직 실행 방지 플래그 설정 또는 구조 변경 필요
                    # 여기서는 그냥 바로 DB 저장 후 리턴하도록 흐름 제어
                    goto_db_save = True 
                else:
                    # 위험 없음 -> 바로 레시피 생성으로 이동
                    goto_recipe_generation = True
                    goto_db_save = False
            except Exception as e:
                print(f"❌ Health Check Error: {e}")
                # 에러 시 안전하게 레시피 생성으로 이동 (혹은 에러 리턴)
                goto_recipe_generation = True
                goto_db_save = False
        else:
            # safety_mode가 있으면 이미 체크 통과한 것
            goto_recipe_generation = True
            goto_db_save = False
            quick_analysis = {}

        # 2. 레시피 생성 (단축 경로)
        if not goto_db_save and goto_recipe_generation:
            _log_recipe_debug("ShortcutRecipeGeneration", {"safety_mode": safety_mode})
            
            try:
                # LangChain Agent를 쓰지 않고 서비스 직접 호출!
                # 필요한 데이터 수집 (Agent가 해주던 일)
                # deficient_nutrients 등은 cached_context에 없을 수 있으므로 None 처리하거나
                # 필요하면 DB에서 다시 조회해야 함. (여기서는 속도를 위해 캐시된 기본값 사용)
                
                # deficient_nutrients가 UserContextCache에 포함되어 있지 않다면
                # 아래에서 None으로 들어가게 됨. (정확도를 위해선 조회 필요하지만 일단 진행)
                # -> food_nutrients_service를 불러와야 하나? 
                # 일단 서비스 내부에서 처리하거나 None으로 넘김.
                
                intent_metadata = {"safety_mode": safety_mode} if safety_mode else None
                
                result = await recipe_service.get_recipe_recommendations(
                    user=current_user,
                    user_request=request.message,
                    llm_user_intent=request.message, # 간단히 원문 사용
                    diseases=diseases,
                    allergies=allergies,
                    has_eaten_today=has_eaten_today,
                    deficient_nutrients=getattr(cached_context, "deficient_nutrients", []), # 캐시에 있으면 사용
                    excess_warnings=getattr(cached_context, "excess_warnings", []),
                    intent_metadata=intent_metadata,
                    meal_type=None, # 자동 감지 맡김
                    safety_mode=safety_mode, # 명시적 전달
                )
                
                # 응답 포맷팅
                response_payload = {
                    "response_id": f"recipe-{uuid.uuid4()}",
                    "action_type": "RECOMMENDATION_RESULT",
                    "message": result.get("user_friendly_message") or "레시피를 찾아봤어요!",
                    "data": {
                        "recipes": result.get("recommendations"),
                        "health_warning": result.get("health_warning"),
                        "inferred_preference": result.get("inferred_preference"),
                        "user_friendly_message": result.get("user_friendly_message"),
                    },
                    "suggestions": ["재료 확인해줘", "다른 메뉴 추천해줘"],
                }
                
                ai_response_payload = json.dumps(response_payload, ensure_ascii=False)
                display_text = response_payload["message"]
                
            except Exception as e:
                print(f"❌ Shortcut Generation Error: {e}")
                # 실패 시 폴백 메시지
                fallback = {
                    "response_id": f"error-{uuid.uuid4()}",
                    "action_type": "TEXT_ONLY",
                    "message": "죄송해요, 레시피를 만드는 도중 문제가 생겼어요. 다시 시도해주시겠어요?",
                    "suggestions": ["다시 시도"],
                }
                ai_response_payload = json.dumps(fallback, ensure_ascii=False)
                display_text = fallback["message"]

    # ----------------------------------------------------------------------
    # [Step 3] 대화 저장 (공통)
    # ----------------------------------------------------------------------
    if is_new_conversation:
        conversation = Conversation(
            session_id=request.session_id,
            user_id=current_user.user_id,
            all_chat="",
            sum_chat=conversation_summary,
            last_message_summarized_at=datetime.utcnow(),
        )
        db.add(conversation)

    new_turn = f"Human: {request.message}\nAI: {display_text}\n\n"
    conversation.all_chat = (conversation.all_chat or "") + new_turn
    conversation.last_message_timestamp = datetime.utcnow()

    db.add_all(
        [
            ChatHistory(
                user_id=current_user.user_id,
                session_id=request.session_id,
                message_type="human",
                content=request.message,
            ),
            ChatHistory(
                user_id=current_user.user_id,
                session_id=request.session_id,
                message_type="ai",
                content=display_text,
            ),
        ]
    )

    await db.commit()

    if not redis_client:
        background_tasks.add_task(
            chat_service.summarize_conversation_if_needed, request.session_id
        )

    return ChatMessageResponse(
        session_id=request.session_id,
        response=ai_response_payload,
        needs_tool_call=needs_tool_call_flag,
    )

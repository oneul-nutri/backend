# 백엔드 리팩토링 TODO

> 목표: 중복 코드 제거 및 구조 정리. 기능 변경 없이 dead code 삭제 + 파일 통합.
> 작업 전 반드시 각 섹션의 "확인사항"을 먼저 읽을 것.

---

## 1. `meals.py` 라우트 핸들러 → 서비스 레이어 분리 ⚠️ 핵심 구조 문제

**현황**
- `save_meal_records`, `save_recommended_meal`, `get_dashboard_stats` 등 모든 핸들러가 SQLAlchemy 쿼리 + 비즈니스 로직을 직접 포함
- `save_recommended_meal`은 LLM 호출 → 영양소 계산 → DB 매칭 → 여러 테이블 INSERT까지 전부 라우트 안에 있음 (약 200줄)
- 파일을 record/history/stats로 분리해도 구조 문제는 그대로임

**작업 방향**
```
현재:  route handler → DB 쿼리 + LLM 호출 직접
목표:  route handler → service layer → DB / LLM
```
- `MealRecordService`: UserFoodHistory 저장, FoodNutrient 조회, HealthScore 생성
- `RecommendedMealService`: 정규화 → LLM 영양소 추론 → DB 매칭 → 저장 전 과정
- 라우트는 의존성 주입 + 서비스 호출 + 응답 직렬화만 담당

**확인사항**
- 서비스 추출 전 반드시 `tests/unit/test_meals_critical.py` 통과 확인 (회귀 방지)
- `health_score_service.py`는 이미 별도 파일이므로 재사용
- 한 핸들러씩 추출 (save → save-recommended → history → dashboard 순)

---

## 2. LangChain → OpenAI SDK 직접 호출로 교체 ⚠️ 높음

**현황**
- `meals.py`의 `get_nutrition_llm()`: `ChatOpenAI` (LangChain wrapper) 사용
- `recipe_recommendation_service.py`, `chat_v2.py` 등 전체 서비스가 LangChain 의존
- LangChain은 버전 의존성이 복잡하고, 단순 LLM 호출에는 오버스펙

**작업 방향**
```
현재: from langchain_openai import ChatOpenAI
목표: from openai import AsyncOpenAI  (또는 httpx 직접 호출)
```
- `langchain_agent.py`를 공통 팩토리로 쓰고 있어서 교체 범위가 넓음
- 점진적으로: meals.py의 `get_nutrition_llm()` 먼저 교체 → chat_v2 → recipe_recommendation_service

**확인사항**
- `grep -rn "langchain" app/ --include="*.py"` 로 의존 범위 전수 파악
- 교체 시 스트리밍 응답 여부 확인 (chat_v2는 스트리밍 사용 중)
- `requirements.txt`에서 `langchain`, `langchain-openai` 제거 후 서버 기동 확인

---

## 3. 테스트 커버리지 확보 (현재 ~9%)

**현황**
- 기존 `tests/unit/test_meals.py`는 삭제된 엔드포인트 기준으로 작성됨 (무효)
- DB 픽스처 없음, 인증 모킹 없음
- LangChain 마이그레이션 전 회귀 방지망이 없는 상태

**작업 방향**
- 목표: **20% 이상** (리팩토링 시작 전 최소 기준)
- 1순위: `POST /meals/save` → `GET /meals/history` → `POST /meals/save-recommended`
- 픽스처: 실제 async 세션 + 트랜잭션 롤백 (`join_transaction_mode="create_savepoint"`)
- 인증: `require_authentication` 의존성 오버라이드로 모킹

**확인사항**
- `tests/unit/test_meals_critical.py` 작성 완료 여부 확인
- `pytest --cov=app tests/` 로 커버리지 측정
- LLM 호출이 있는 엔드포인트(`save-recommended`)는 `unittest.mock.patch`로 LLM 모킹

---

## 4. `meals.py` 라우트 파일 분리 (3번 이후, 별도 PR)

**현황**
- `app/api/v1/routes/meals.py`가 단일 파일로 비대해진 상태
- 구조 문제(1번)를 먼저 해결한 뒤 파일 분리를 해야 의미가 있음

**작업 방향** (1번 서비스 추출 완료 후)
```
routes/meals/
├── __init__.py
├── record.py     # POST /meals/save, /save-recommended, /preview-nutrition
├── history.py    # GET /meals/history, /meals/daily
└── stats.py      # GET /meals/dashboard-stats, /meals/score
```

**확인사항**
- 반드시 서비스 레이어 분리(1번) 완료 후 진행
- `router.py`의 prefix("/meals") 유지
- 분리 전 전체 엔드포인트 목록: `grep -n "^@router\." app/api/v1/routes/meals.py`

---

## 5. lifespan 훅 추가 (낮음)

**현황**
- 앱 종료 시 DB 커넥션 풀, Redis 클라이언트 명시적 정리 없음
- 프로덕션 환경에서 graceful shutdown 미보장

**작업**
```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()
    await redis_client.close()

app = FastAPI(lifespan=lifespan)
```

---

## 작업 순서 권장

| 순서 | 작업 | 난이도 | 리스크 |
|------|------|--------|--------|
| 1 | 테스트 커버리지 확보 (3번) | 보통 | 없음 |
| 2 | meals.py 서비스 레이어 분리 (1번) | 어려움 | 중간 |
| 3 | LangChain → OpenAI SDK (2번) | 어려움 | 높음 |
| 4 | meals.py 파일 분리 (4번) | 보통 | 낮음 |
| 5 | lifespan 훅 (5번) | 쉬움 | 없음 |

## 공통 주의사항

- `__pycache__` 는 자동 재생성되므로 신경 쓰지 말 것
- 각 작업 후 `uvicorn app.main:app --reload` 로 서버 기동 확인
- 삭제 전 반드시 grep으로 참조 여부 확인
- 기능 추가나 로직 변경은 이 TODO 범위 밖

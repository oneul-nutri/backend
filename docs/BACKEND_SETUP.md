# 백엔드 셋업 가이드

이 문서는 [oneul-nutri/main의 CLAUDE.md "로컬 실행" 섹션](https://github.com/oneul-nutri/main/blob/main/CLAUDE.md#로컬-실행)으로 대체됨.

## 빠른 시작

워크스페이스가 셋업된 상태라면:

```bash
cd ~/workspace/oneul-nutri
mise run local:back        # Redis Docker 자동 + uvicorn --reload (8000)
```

신규 셋업이라면:

```bash
git clone https://github.com/oneul-nutri/main.git oneul-nutri
cd oneul-nutri
mise install               # uv 자동 설치
mise run init              # 모든 sub-repo clone + .env + venv + npm install
mise run local:back
```

## 환경 변수

[`ENV_SETUP_GUIDE.md`](./ENV_SETUP_GUIDE.md) 참조.

## 인증·DB·기타 컴포넌트

- 세션 인증: [`SESSION_AUTH_GUIDE.md`](./SESSION_AUTH_GUIDE.md) (Redis 기반, JWT 아님)
- Redis 셋업: [`REDIS_SETUP_GUIDE.md`](./REDIS_SETUP_GUIDE.md) (운영 외 로컬은 mise가 Docker로 자동 기동)
- AI 파이프라인: [`YOLO_GPT_VISION_SETUP.md`](./YOLO_GPT_VISION_SETUP.md)

---

**갱신 이력**: 2026-04-28 LangChain 제거(TODO #4) + uv 도입 + oneul-nutri 마이그레이션 반영하여 옛 setup 정보(JWT, fcv_user, food_calorie DB, workers/ 디렉터리, chatbot.py 등) 일괄 폐기. 단일 source of truth는 root CLAUDE.md.

# 프론트엔드-백엔드 통합 가이드

## 아키텍처 개요

```
┌─────────────────┐
│   Next.js 앱    │
│  (포트 3000)    │
└────────┬────────┘
         │
         │ /api/v1/* 호출
         │ utils/api.ts 헬퍼 사용 (frontend)
         ▼
┌─────────────────┐
│  FastAPI 백엔드  │
│  (포트 8000)    │
│   /api/v1/*     │
└─────────────────┘
```

**local 모드**: 프론트는 `NEXT_PUBLIC_API_URL=http://localhost:8000`으로 직접 호출.
**deploy 모드**: 프론트는 Docker Nginx 뒤에 있고, Nginx가 `host.docker.internal:8000` 백엔드로 `/api/v1/*` 프록시. 외부 진입은 Cloudflare Tunnel.

## 실행

[oneul-nutri/main의 CLAUDE.md "로컬 실행" 섹션](https://github.com/oneul-nutri/main/blob/main/CLAUDE.md#로컬-실행) 참조.

## API 매핑

OpenAPI 스키마가 source of truth. Swagger UI에서 확인:
- http://localhost:8000/docs (local 백엔드 기동 시)

## 프론트엔드 API 호출 규칙

`utils/api.ts`의 fetch 헬퍼를 통해서만 호출. 직접 `fetch()`에 URL 하드코딩 금지. (프론트 `CLAUDE.md` 또는 root `CLAUDE.md` 작업 규칙 참조.)

## CORS

백엔드 `.env`의 `cors_allow_origins`로 제어. 기본값은 `http://localhost:3000`.

## 인증

세션 기반 (Redis). JWT 아님. [`SESSION_AUTH_GUIDE.md`](./SESSION_AUTH_GUIDE.md) 참조.

## 디버깅

- 백엔드 로그: uvicorn stdout (자동 요청 로그 출력)
- 프론트 네트워크: 브라우저 DevTools → Network 탭
- API 직접 테스트: `curl http://localhost:8000/api/v1/...` 또는 Swagger UI

## 일반 오류

| 증상 | 원인·해결 |
|------|---------|
| `Access-Control-Allow-Origin` | 백엔드 `.env`의 `cors_allow_origins`에 프론트 origin 추가 후 백엔드 재시작 |
| `ECONNREFUSED` | 백엔드 미기동 또는 포트 충돌. `mise run local:back:stop` 후 재기동 |

---

**갱신 이력**: 2026-04-28 폴더명·인증 방식·stub 데이터 등 옛 정보 일괄 정정. API 매핑 표는 OpenAPI 스키마로 위임 (코드 변경 시 자동 반영).

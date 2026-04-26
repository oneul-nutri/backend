# Docker CI/CD Setup

이 저장소는 Docker 기반 배포를 전제로 다음 두 흐름을 갖습니다.

- CI: GitHub Actions가 이미지를 빌드하고 컨테이너를 띄운 뒤 `/healthz` 응답을 확인합니다.
- CD: `main` 브랜치 push 시 self-hosted runner 에서 `docker compose up -d --build`를 실행합니다.

## 1. 로컬 서버 준비

이 문서는 AWS가 아니라 현재 PC를 배포 서버로 쓰는 전제를 기준으로 작성되었습니다.

필수 조건:

- Docker Engine 또는 Docker Desktop 설치
- Docker Compose 사용 가능
- GitHub self-hosted runner 설치
- 8000 포트 접근 허용
- `.env`에 실제 운영 값 입력

## 2. 첫 실행

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:8000/healthz
```

정상 응답:

```json
{"status":"ok"}
```

## 3. self-hosted runner 연결

배포를 자동화하려면 이 컴퓨터를 GitHub Actions self-hosted runner 로 등록해야 합니다.

권장 사항:

- runner는 이 저장소 전용으로 사용
- runner 계정은 Docker 실행 권한 보유
- `.env`는 runner 작업 디렉터리에서 유지
- 운영 시크릿은 Git에 커밋하지 않음

GitHub 저장소에서 다음 순서로 등록합니다.

1. `Settings`
2. `Actions`
3. `Runners`
4. `New self-hosted runner`

등록 후 `main` 브랜치에 push 하면 `.github/workflows/docker-cd.yml` 이 실행됩니다.

## 4. 환경 변수

반드시 수정해야 하는 값:

- `DATABASE_URL`
- `SESSION_SECRET_KEY`
- `OPENAI_API_KEY`

로컬 PC의 MySQL 을 그대로 쓸 때는 `host.docker.internal` 경로를 유지해도 됩니다.
Linux 환경에서는 `docker-compose.yml` 에 `host-gateway` 매핑이 이미 들어가 있습니다.

## 5. 운영 주의사항

- 현재 워크플로우는 smoke test 중심입니다. 애플리케이션 테스트 스위트가 안정화되면 CI 단계에 추가하세요.
- `/healthz` 는 앱 기동 확인용입니다. DB 연결 확인까지 포함한 readiness check 가 필요하면 별도 엔드포인트를 추가하세요.
- HTTPS 는 Docker Compose 바깥에서 Nginx 또는 Caddy reverse proxy 로 처리하는 편이 안전합니다.
- 모델 파일이 크면 이미지에 굽지 말고 `models/` 볼륨으로 주입하는 편이 낫습니다.


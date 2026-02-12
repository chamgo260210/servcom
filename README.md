# Dasan Shift Manager

대학 도서관(다산정보관) 근로장학생 근무 관리용 풀스택 예제입니다.

- 백엔드: FastAPI (`/backend`)
- 프론트엔드: 정적 HTML/CSS/JS (`/ui`)
- DB: PostgreSQL 스키마/마이그레이션 (`/db`)

## 저장소 구조
- `backend/`: API 서버, JWT 인증, 역할 기반 권한(MASTER > OPERATOR > MEMBER)
- `ui/`: 정적 프론트엔드, API 호출은 기본적으로 상대경로 `/api`
- `db/schema.sql`: 최종 기준 스키마
- `db/migrations/`: 순차 마이그레이션
- `deploy/`: Nginx/systemd/cloudflared/Worker 배포 템플릿
- `docs/servcom_deployment_guide.md`: 시설망(outbound-only) + Tailscale SSH 포함 완전 초기 재현 가이드

## 환경 변수 (필수)
- `DATABASE_URL`
- `JWT_SECRET`

선택:
- `ACCESS_TOKEN_EXPIRE_MINUTES` (기본 60)
- `TRUSTED_HOSTS` (기본 `localhost,127.0.0.1,*.trycloudflare.com,*.cfargotunnel.com,*.workers.dev`)
- `TRUST_ALL_HOSTS` (기본 `false`, 장애 대응용 임시 전체 허용)
- `BACKEND_CORS_ORIGINS` (기본 비활성)
- `MASTER_LOGIN_ID`/`MASTER_PASSWORD`/`MASTER_NAME`/`MASTER_IDENTIFIER` (users 테이블이 비어 있을 때 시작 시 부트스트랩 마스터 자동 생성)

예시는 `deploy/.env.server.example` 참고.

## 로컬 실행
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

## DB 초기화
```bash
psql "$DATABASE_URL" -f db/schema.sql
for f in db/migrations/*.sql; do
  psql "$DATABASE_URL" -f "$f"
done
```


## 신규 VM 배포 후 첫 로그인까지 빠른 체크리스트
1. `.env`에 DB/JWT/Cloudflare 값 + `MASTER_*` 설정
2. DB 스키마/마이그레이션 적용
3. `work-time-api`, `work-time-cloudflared` 서비스 기동
4. Worker `/_edge/status`에서 `has_active_url=true` 확인
5. 브라우저에서 Worker URL 접속 → 로그인

> 참고: `MASTER_*`는 SQL seed가 아니라 **API 시작 시 users가 비어 있을 때 자동 생성되는 부트스트랩 계정**입니다.

## 시설망 서버컴 배포
아래 문서를 순서대로 진행하세요(우분투만 설치된 초기 서버 기준).

- `docs/servcom_deployment_guide.md`

이 문서에는 다음이 포함됩니다.
1. 아키텍처/선택 근거
2. `/srv/app` 표준 디렉토리
3. 코드/설정 반영 포인트
4. 처음부터 재현 가능한 설치 명령어
5. 보안 위협 분석 및 최소 대응

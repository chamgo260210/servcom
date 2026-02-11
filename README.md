# Dasan Shift Manager

대학 도서관(다산정보관) 근로장학생 근무 관리용 풀스택 샘플입니다. 프론트엔드는 정적 페이지(GitHub Pages), 백엔드는 FastAPI(Render), 데이터베이스는 PostgreSQL(Neon)을 사용합니다. 저장소에는 코드만 포함되고, 데이터베이스 URL·JWT 시크릿은 환경 변수로만 주입하도록 강제해 비밀이 커밋되지 않도록 했습니다.

## 저장소 구조
- `/ui`: 정적 프론트엔드 (HTML/CSS/JS). GitHub Pages에 `/ui`를 퍼블리시하면 루트 `index.html`이 로그인 페이지로 리다이렉트합니다.
- `/backend`: FastAPI 애플리케이션.
- `/db`: PostgreSQL 스키마와 시드(Neon에서 실행).

## 데이터베이스 설정 (Neon)
1. 새 PostgreSQL 데이터베이스 인스턴스를 만듭니다.
2. 스키마를 실행합니다.
   ```sql
   \i db/schema.sql
   ```
3. 샘플 데이터를 넣습니다(선택 사항).
   ```sql
   \i db/seed.sql
   ```

### 기존 DB 업데이트
이전 배포본에는 `request_status` enum의 `CANCELLED` 값과 부분 결근 시간·취소 플래그 컬럼이 없을 수 있습니다. 이미 생성된 데이터베이스에서는 다음 구문으로 enum과 컬럼을 추가해 주세요.

```sql
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_type WHERE typname = 'request_status') THEN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_type t
            JOIN pg_enum e ON t.oid = e.enumtypid
            WHERE t.typname = 'request_status'
              AND e.enumlabel = 'CANCELLED'
        ) THEN
            ALTER TYPE request_status ADD VALUE IF NOT EXISTS 'CANCELLED';
        END IF;
    END IF;
    ALTER TABLE IF EXISTS shift_requests
        ADD COLUMN IF NOT EXISTS target_start_time TIME,
        ADD COLUMN IF NOT EXISTS target_end_time TIME,
        ADD COLUMN IF NOT EXISTS cancelled_after_approval BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS cancel_reason TEXT;
END$$;
```

## 백엔드 환경 변수
Render나 로컬 실행 시 다음 환경 변수를 설정해야 합니다(미설정 시 애플리케이션이 시작되지 않음).
- `DATABASE_URL` (예: `postgresql://user:pass@host:5432/dbname`)
- `JWT_SECRET` (임의의 안전한 문자열)
- `ACCESS_TOKEN_EXPIRE_MINUTES` (선택, 기본 60)
- `BACKEND_CORS_ORIGINS` (쉼표 구분, 기본 `*`)

## 로컬에서 백엔드 실행
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r backend/requirements.txt
uvicorn backend.main:app --reload
```

## Render에 백엔드 배포
1. GitHub 저장소를 Render Web Service로 연결합니다.
2. Build 명령: `pip install -r backend/requirements.txt`
3. Start 명령: `./render_start.sh` (스크립트가 자동으로 저장소 루트로 이동하고 `PYTHONPATH`에 루트를 추가해 `ModuleNotFoundError: No module named 'backend'`를 방지합니다.)
4. 환경 변수 설정: `DATABASE_URL`, `JWT_SECRET`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `BACKEND_CORS_ORIGINS`.

## GitHub Pages로 프론트엔드 배포
1. `/ui` 디렉터리를 GitHub Pages 대상으로 퍼블리시합니다.
2. `/ui/js/api.js`의 `API_BASE_URL`을 Render에 배포된 백엔드 URL로 교체합니다.
3. GitHub Pages 루트가 `/ui`면 `index.html`이 자동으로 `html/index.html`로 리다이렉션합니다.

## 핵심 API 엔드포인트
- `POST /auth/login` — JWT 발급.
- `GET /auth/me` — 현재 사용자.
- `PATCH /auth/password` — 비밀번호 변경.
- `GET/POST/PATCH/DELETE /users` — 사용자 관리(권한 인가 필요).
- `GET /schedule/global`, `POST /schedule/shifts`, `POST /schedule/assign` — 일정 관리.
- `POST /requests`, `/requests/pending`, `/requests/{id}/approve|reject` — 결근/추가 근무 요청 흐름.
- `GET /admin/audit-logs` — 마스터의 감사 로그 조회.

## 참고 사항
- 역할 계층: MASTER > OPERATOR > MEMBER (상위 역할은 하위 역할의 기능을 모두 볼 수 있음).
- 시간대 가정: Asia/Seoul.
- 시드 사용자 예시: `master/Master123!`, `operator/Operator123!`, 멤버 `kim|lee|park/Member123!`.

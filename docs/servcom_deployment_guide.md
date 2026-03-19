# 시설망 서버컴 배포 실전 가이드 (완전 초기 상태 기준)

> 대상 독자: **Ubuntu Server만 설치된 서버컴**에서, 도메인/유료플랜 없이, Cloudflare Quick Tunnel + Worker + KV 구조로 외부 서비스 공개를 해야 하는 운영자

---

## 0. 목표/제약 재확인

이 문서는 아래 조건을 만족하도록 작성되었습니다.

- 서버컴은 시설망에 있고 **공인 IP 인바운드 불가**
- 외부 공개는 **Cloudflare Quick Tunnel + Worker + KV**만 사용
- 도메인 구매 없음, Cloudflare 무료 플랜 기준
- 기존 앱 기능(프론트 + FastAPI + PostgreSQL) 유지
- SSH 관리 접속은 **Tailscale** 사용

---


## 0-1. 신규 VM에서 첫 로그인까지 초압축 순서

1. 서버 기본 세팅/코드 배치/의존성 설치
2. PostgreSQL 생성 후 `db/schema.sql` + `db/migrations/*.sql` 적용
3. `/srv/app/.env` 작성 (`DATABASE_URL`, `JWT_SECRET`, `CF_*`, `MASTER_*`)
4. systemd 반영 후 `work-time-api`, `work-time-cloudflared` 시작
5. `https://<worker-url>/_edge/status` 확인 (`has_active_url=true`)
6. Worker URL 접속 후 `MASTER_LOGIN_ID` / `MASTER_PASSWORD`로 첫 로그인

> `MASTER_*`는 SQL 파일에 들어가는 정적 seed가 아니라, API 시작 시 users가 비어 있으면 자동 생성되는 런타임 부트스트랩 계정입니다.

---

## 1. 최종 아키텍처

```text
[관리자 PC] --(Tailscale SSH)--> [서버컴 Ubuntu]

[외부 사용자]
   -> [Cloudflare Worker URL (고정)]
   -> [KV active_url 조회]
   -> [Quick Tunnel URL (가변, trycloudflare.com)]
   -> [Nginx :8080]
   -> [/api/* reverse proxy]
   -> [FastAPI :8000]
   -> [PostgreSQL]
```

### 왜 이렇게 구성하나?

1. 시설망에서는 inbound가 막혀 있으므로 서버가 외부로 나가는 연결(outbound)만 활용해야 합니다.
2. Quick Tunnel은 서버가 Cloudflare에 outbound 연결을 유지하므로 인바운드 개방이 필요 없습니다.
3. Quick Tunnel URL은 재시작 시 바뀌므로, Worker가 KV의 `active_url`을 읽어 프록시(fetch) 또는 redirect 하도록 하면 사용자 진입 URL은 고정됩니다.

---



### 운영 참고 문서

- Cloudflare/터널 장애 단계 진단: `docs/cloudflare_tunnel_troubleshooting.md`
- 코드 변경 배포 절차: `docs/change_rollout_runbook.md`

---

## 2. 서버 파일 구조(권장 표준)

```text
/srv/app/
  ├─ backend/
  ├─ ui/
  ├─ db/
  ├─ deploy/
  ├─ docs/
  ├─ scripts/
  │   └─ cloudflared-kv-updater.sh
  ├─ venv/
  └─ .env
```

---

## 3. 0부터 시작: 서버 초기 세팅 (Ubuntu 22.04+)

## 3-1. 최초 접속/기본 패키지

```bash
sudo apt update
sudo apt -y upgrade
sudo apt -y install git curl wget jq unzip ca-certificates gnupg lsb-release software-properties-common
```

## 3-2. 시간대/로케일

```bash
sudo timedatectl set-timezone Asia/Seoul
timedatectl
```

## 3-3. 운영 계정/권한(선택)

운영 계정을 별도로 쓰는 것을 권장합니다.

```bash
# 예시: appadmin 계정 생성
sudo adduser appadmin
sudo usermod -aG sudo appadmin
```

---

## 4. Tailscale 기반 SSH 관리 접속 구성

> 요구사항 반영: 외부 SSH는 공인망이 아니라 Tailscale 네트워크만 사용

## 4-1. 서버에 Tailscale 설치

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

위 명령 후 콘솔에 표시되는 URL로 브라우저 로그인하여 노드를 승인합니다.

## 4-2. 서버 Tailscale IP 확인

```bash
tailscale ip -4
tailscale status
```

## 4-3. 관리자 PC에서 SSH 접속 테스트

```bash
ssh <서버유저>@<서버의_tailscale_ip>
```

## 4-4. 보안 권장 (선택)

- `/etc/ssh/sshd_config`에서 PasswordAuthentication 비활성 검토
- UFW는 SSH를 Tailscale 인터페이스 기준으로 허용

예시:

```bash
sudo ufw allow in on tailscale0 to any port 22 proto tcp
sudo ufw enable
sudo ufw status verbose
```

---

## 5. 앱 코드 배치

```bash
sudo mkdir -p /srv/app
sudo chown -R $USER:$USER /srv/app
cd /srv/app
git clone <이 저장소 URL> .
```

브랜치/커밋 고정 배포를 권장합니다.

```bash
git checkout <배포할 브랜치_or_tag_or_commit>
```

---

## 6. Python/FastAPI 실행환경 구성

## 6-1. Python 설치

Ubuntu 22.04 기본 python3를 사용하되, pip/venv 확인:

```bash
python3 --version
sudo apt -y install python3-pip python3-venv
```

## 6-2. 가상환경 생성/의존성 설치

```bash
cd /srv/app
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements.txt
```

---

## 7. PostgreSQL 설치/초기화 (서버 로컬)

## 7-1. 설치/기동

```bash
sudo apt -y install postgresql postgresql-contrib
sudo systemctl enable --now postgresql
sudo systemctl status postgresql --no-pager
```

## 7-2. DB/계정 생성

```bash
sudo -u postgres psql
```

psql에서:

```sql
CREATE USER worktime WITH PASSWORD '강력한비밀번호';
CREATE DATABASE work_time OWNER worktime;
\q
```

## 7-3. 접속 테스트

`!`, `@`, `#` 같은 특수문자가 비밀번호에 있으면 Bash 히스토리 확장(`event not found`)이 발생할 수 있습니다.
아래 방법 중 하나를 사용하세요.

```bash
# 방법 A) URL 전체를 작은따옴표로 감싸기 (권장)
psql 'postgresql://worktime:강력한비밀번호@127.0.0.1:5432/work_time' -c 'SELECT 1;'

# 방법 B) PGPASSWORD 사용
PGPASSWORD='강력한비밀번호' psql -h 127.0.0.1 -U worktime -d work_time -c 'SELECT 1;'

# 방법 C) 히스토리 확장 끄기 (현재 셸 세션 한정)
set +H
psql "postgresql://worktime:강력한비밀번호@127.0.0.1:5432/work_time" -c "SELECT 1;"
```

> 참고: `postgresql.service`가 `active (exited)`로 보여도 Ubuntu의 wrapper 서비스 특성상 정상일 수 있습니다.
> 실제 DB 프로세스는 아래로 확인하세요.

```bash
sudo systemctl status postgresql@14-main --no-pager
pg_isready -h 127.0.0.1 -p 5432
```

---

## 8. DB 스키마/마이그레이션 적용

본 저장소는 최종 스키마와 순차 마이그레이션을 함께 사용합니다.

```bash
cd /srv/app
export DATABASE_URL='postgresql://worktime:강력한비밀번호@127.0.0.1:5432/work_time'
# 비밀번호에 특수문자가 많다면 DATABASE_URL 대신 PGPASSWORD 방식 권장

psql "$DATABASE_URL" -f db/schema.sql
for f in db/migrations/*.sql; do
  echo "[MIGRATE] $f"
  psql "$DATABASE_URL" -f "$f"
done
```

`db/migrations/0013_serial_layout_and_shelf_type_extensions.sql`까지 적용되었는지 확인:

```bash
psql "$DATABASE_URL" -c "\dt"
```

---

## 9. 프론트 + 백엔드 + 프록시 구조 반영 요약

이미 코드에서 다음이 반영되어 있습니다.

- 프론트 API 기본 경로 `/api` (동일 오리진) 사용
- FastAPI는 `TRUSTED_HOSTS`, `API_ROOT_PATH`, env 기반 CORS 처리
- Nginx는 `/` 정적 + `/api/` FastAPI 프록시

실제 설정 파일은 아래를 사용합니다.

- `deploy/nginx/work-time.conf`
- `deploy/systemd/work-time-api.service`
- `deploy/systemd/work-time-cloudflared.service`
- `deploy/scripts/cloudflared-kv-updater.sh`
- `deploy/cloudflare/worker.js`

---

## 10. 환경변수(.env) 초상세 설정

## 10-1. 템플릿 복사

```bash
cd /srv/app
cp deploy/.env.server.example .env
# 기본 권장: 소유자만 읽기/쓰기
chmod 600 .env
```

## 10-2. 항목별 의미/값 소스

`.env`를 열고 아래처럼 채웁니다.

```dotenv
APP_ENV=production
PROJECT_NAME=Dasan Shift Manager
DATABASE_URL=postgresql+psycopg2://worktime:<DB비밀번호>@127.0.0.1:5432/work_time
JWT_SECRET=<랜덤_긴_문자열>
ACCESS_TOKEN_EXPIRE_MINUTES=60
API_ROOT_PATH=
TRUSTED_HOSTS=localhost,127.0.0.1,*.trycloudflare.com,*.cfargotunnel.com,*.workers.dev
TRUST_ALL_HOSTS=false
BACKEND_CORS_ORIGINS=
BACKEND_CORS_ALLOW_CREDENTIALS=false

MASTER_LOGIN_ID=master
MASTER_PASSWORD=<초기마스터비밀번호>
MASTER_NAME=Master Admin
MASTER_IDENTIFIER=MASTER_DEFAULT

CF_ACCOUNT_ID=<Cloudflare_Account_ID>
CF_API_TOKEN=<KV쓰기권한_API_Token>
CF_KV_NAMESPACE_ID=<KV_Namespace_ID>
KV_KEY=active_url
TUNNEL_HOST_FILTER=trycloudflare.com,cfargotunnel.com
TUNNEL_HOST_DENY=api.trycloudflare.com
KV_UPDATED_AT_KEY=active_url_updated_at
SANITIZE_EXISTING_KV=false
CLEAR_KV_ON_429=false
RATE_LIMIT_COOLDOWN_SECONDS=300
NORMAL_RETRY_SECONDS=5
WORKER_REFRESH_URL=https://<worker-url>/_edge/refresh
WORKER_REFRESH_TOKEN=<worker-cache-refresh-token>
REQUIRE_RESOLVABLE_TUNNEL_HOST=false
LOCAL_URL=http://127.0.0.1:8080
LOG_DIR=/var/log/work-time
# true면 KV 업데이트 없이 터널만 실행(디버깅용)
SKIP_KV_UPDATE=false
```

### 값은 어디서 가져오나?

- `DATABASE_URL`: 직접 생성한 PostgreSQL 접속 문자열
- `JWT_SECRET`: 서버에서 생성 가능
  ```bash
  openssl rand -hex 32
  ```
- `CF_ACCOUNT_ID`: Cloudflare Dashboard 우측/개요 영역의 Account ID
- `CF_API_TOKEN`: Cloudflare API Token 생성(아래 13장)
- `CF_KV_NAMESPACE_ID`: Workers & Pages > KV에서 namespace 생성 후 ID 복사

---

## 11. Nginx 설치/적용

## 11-1. 설치

```bash
sudo apt -y install nginx
sudo systemctl enable --now nginx
```

## 11-2. 설정 배치

```bash
sudo cp /srv/app/deploy/nginx/work-time.conf /etc/nginx/sites-available/work-time
sudo ln -sf /etc/nginx/sites-available/work-time /etc/nginx/sites-enabled/work-time
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl restart nginx
# 한글 깨짐이 있으면 work-time.conf에 `charset utf-8;` 확인
```

## 11-3. 로컬 테스트

```bash
curl -i http://127.0.0.1:8080/
curl -i http://127.0.0.1:8080/health
```

---

## 12. FastAPI systemd 등록

## 12-1. 서비스 파일 배치

```bash
sudo cp /srv/app/deploy/systemd/work-time-api.service /etc/systemd/system/work-time-api.service
sudo systemctl daemon-reload
sudo systemctl enable --now work-time-api.service
# 서비스 파일 변경 후에는 restart 필수
sudo systemctl restart work-time-api.service
```

## 12-2. 상태/로그 확인

```bash
systemctl status work-time-api --no-pager
journalctl -u work-time-api -n 200 --no-pager
curl -fsS http://127.0.0.1:8000/health
```

## 12-3. 현재 발생한 오류(`ModuleNotFoundError: No module named app`) 해결

해당 오류는 systemd의 실행 기준 경로가 `/srv/app`인데, 코드가 `from app import ...` 구조여서
파이썬 import path에 `/srv/app/backend`가 포함되지 않아 발생합니다.

이미 본 저장소의 서비스 파일은 아래처럼 수정되어 있습니다.

- `WorkingDirectory=/srv/app/backend`
- `Environment=PYTHONPATH=/srv/app/backend`
- `ExecStart=... uvicorn main:app ...`

서버에서 반드시 다시 반영하세요.

```bash
sudo cp /srv/app/deploy/systemd/work-time-api.service /etc/systemd/system/work-time-api.service
sudo systemctl daemon-reload
sudo systemctl restart work-time-api.service

systemctl status work-time-api --no-pager
journalctl -u work-time-api -n 100 --no-pager
curl -fsS http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8080/health
```

## 12-4. 현재 발생한 오류(`PermissionError: /srv/app/.env`) 해결

로그에 아래 메시지가 보이면 `.env` 파일 읽기 권한 이슈입니다.

- `PermissionError: [Errno 13] Permission denied: '/srv/app/.env'`

원인
- 서비스는 `www-data` 사용자로 실행되는데, `.env`가 너무 제한적으로 설정되면(`600` + 타 사용자 소유) 앱에서 직접 `.env`를 읽을 때 실패할 수 있습니다.

현재 코드에서는 **읽을 수 없는 `.env`를 무시**하도록 보강했습니다.
또한 systemd `EnvironmentFile=/srv/app/.env`는 root가 읽어 환경변수 주입을 수행하므로, 런타임에 앱이 `.env`를 직접 못 읽어도 동작 가능합니다.

권한을 명시적으로 정리하려면 아래 중 하나를 사용하세요.

```bash
# 방법 A) root 소유 + www-data 그룹 읽기 허용
sudo chown root:www-data /srv/app/.env
sudo chmod 640 /srv/app/.env

# 방법 B) 서비스 실행 사용자로 소유권 부여(운영정책에 맞게 선택)
sudo chown www-data:www-data /srv/app/.env
sudo chmod 600 /srv/app/.env

# 반영
sudo systemctl daemon-reload
sudo systemctl restart work-time-api.service
```

검증:

```bash
systemctl status work-time-api --no-pager
journalctl -u work-time-api -n 100 --no-pager
curl -fsS http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8080/health
```

---

## 13. Cloudflare 설정 (Dashboard 클릭 경로까지)

## 13-1. 계정 준비

- Cloudflare 로그인
- 무료 플랜 계정으로 진행

## 13-2. KV Namespace 생성

1. Dashboard 좌측: **Workers & Pages**
2. 메뉴: **KV**
3. **Create namespace** 클릭
4. 이름 예시: `work-time-kv`
5. 생성 후 **Namespace ID** 복사 (`CF_KV_NAMESPACE_ID`에 입력)

## 13-3. API Token 생성 (KV 쓰기용)

1. 우측 상단 프로필 > **My Profile**
2. **API Tokens** 탭
3. **Create Token**
4. 템플릿이 없으면 **Create Custom Token**
5. 권한 예시(최소권한 권장):
   - Account > Workers KV Storage > Edit
6. Account Resources: 해당 계정 선택
7. 생성 완료 후 토큰 값을 복사 (`CF_API_TOKEN`)

## 13-4. Worker 생성/배포

1. Dashboard > **Workers & Pages**
2. **Create** > **Worker**
3. 에디터 코드 전체를 `deploy/cloudflare/worker.js` 내용으로 교체
4. Worker Settings > Variables에서 추가:
   - `ALLOWED_TUNNEL_HOSTS=trycloudflare.com,cfargotunnel.com`
   - `DENIED_TUNNEL_HOSTS=api.trycloudflare.com`
   - `MAX_ACTIVE_URL_AGE_SECONDS=1800` (권장, stale URL 차단)
   - `ACTIVE_URL_UPDATED_AT_KEY=active_url_updated_at`
   - `ACTIVE_URL_CACHE_TTL_SECONDS=3600` (권장: 장기 캐시 + 트리거 갱신)
   - `CACHE_REFRESH_TOKEN=<random-token>` (필수: 캐시 갱신 엔드포인트 보호)
5. Worker Settings > Bindings > KV Namespace 바인딩 추가:
   - Variable name: `TUNNEL_KV`
   - Namespace: `work-time-kv`
6. Deploy

배포 후 Worker URL 예시:
- `https://work-time-gateway.<subdomain>.workers.dev`

이 URL이 사용자 고정 진입 URL이 됩니다.

추가 디버그 엔드포인트:
- `https://<worker-url>/_edge/status`
  - `has_active_url=true` 이어야 리다이렉트가 동작합니다.
  - `active_age_seconds`가 비정상적으로 크면 stale URL 가능성이 큽니다.

> 비용/사용량 관점에서 기본 권장은 **KV 단일 저장소 + Worker 장기 캐시 + 변경시점 트리거 갱신** 입니다.
> D1를 함께 쓰면 write 경로가 늘어나 운영 복잡도와 실패 지점이 증가할 수 있습니다.

---

## 14. cloudflared 설치 + 자동 KV 업데이트

## 14-1. cloudflared 설치

```bash
which cloudflared || (
  curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb &&
  sudo dpkg -i /tmp/cloudflared.deb
)
cloudflared --version
```

## 14-2. 스크립트 배치

```bash
sudo mkdir -p /srv/app/scripts
sudo cp /srv/app/deploy/scripts/cloudflared-kv-updater.sh /srv/app/scripts/cloudflared-kv-updater.sh
sudo chmod +x /srv/app/scripts/cloudflared-kv-updater.sh
sudo cp /srv/app/deploy/systemd/work-time-cloudflared.service /etc/systemd/system/work-time-cloudflared.service
sudo mkdir -p /var/log/work-time
sudo chown -R www-data:www-data /var/log/work-time
```

## 14-3. systemd 등록

> **중요:** 아래 값이 `.env`에 채워지지 않으면 서비스는 `exit-code`로 실패합니다.
> - `CF_ACCOUNT_ID`
> - `CF_API_TOKEN`
> - `CF_KV_NAMESPACE_ID`

```bash
# 값이 비어있는지 먼저 확인
sudo grep -E '^(CF_ACCOUNT_ID|CF_API_TOKEN|CF_KV_NAMESPACE_ID)=' /srv/app/.env

sudo cp /srv/app/deploy/systemd/work-time-cloudflared.service /etc/systemd/system/work-time-cloudflared.service
sudo cp /srv/app/deploy/scripts/cloudflared-kv-updater.sh /srv/app/scripts/cloudflared-kv-updater.sh
sudo chmod +x /srv/app/scripts/cloudflared-kv-updater.sh
sudo cp /srv/app/deploy/systemd/work-time-cloudflared.service /etc/systemd/system/work-time-cloudflared.service
sudo systemctl daemon-reload
sudo systemctl enable --now work-time-cloudflared.service
```

## 14-4. 동작 확인

```bash
systemctl status work-time-cloudflared --no-pager
journalctl -u work-time-cloudflared -n 200 --no-pager
```

정상이라면 로그에 아래가 보여야 합니다.
- `Active tunnel URL: https://...trycloudflare.com`
- `KV key 'active_url' updated and verified`

추가 검증(Worker 측):

```bash
curl -fsS https://<worker-url>/_edge/status
```

`has_active_url`가 `true`인지 확인하세요.

## 14-5. 현재 발생한 오류(`CF_ACCOUNT_ID is required`) 해결

해당 메시지는 스크립트/서비스 이상이 아니라 **Cloudflare 연동용 환경변수 미설정** 상태입니다.

- `/srv/app/scripts/cloudflared-kv-updater.sh: ... CF_ACCOUNT_ID is required`

해결 절차:

```bash
sudo editor /srv/app/.env
# 아래 3개를 실제 값으로 채움
# CF_ACCOUNT_ID=...
# CF_API_TOKEN=...
# CF_KV_NAMESPACE_ID=...
# SKIP_KV_UPDATE=false  # 운영 모드

sudo cp /srv/app/deploy/scripts/cloudflared-kv-updater.sh /srv/app/scripts/cloudflared-kv-updater.sh
sudo chmod +x /srv/app/scripts/cloudflared-kv-updater.sh
sudo cp /srv/app/deploy/systemd/work-time-cloudflared.service /etc/systemd/system/work-time-cloudflared.service
sudo cp /srv/app/deploy/systemd/work-time-cloudflared.service /etc/systemd/system/work-time-cloudflared.service
sudo systemctl daemon-reload
sudo systemctl reset-failed work-time-cloudflared.service
sudo systemctl restart work-time-cloudflared.service
systemctl status work-time-cloudflared --no-pager
journalctl -u work-time-cloudflared -n 100 --no-pager
curl -fsS https://<worker-url>/_edge/status
```

만약 KV 업데이트 없이 터널 URL만 먼저 확인하려면:

```bash
sudo sed -i 's/^SKIP_KV_UPDATE=.*/SKIP_KV_UPDATE=true/' /srv/app/.env
sudo systemctl restart work-time-cloudflared.service
journalctl -u work-time-cloudflared -n 100 --no-pager
```

> `SKIP_KV_UPDATE=true`는 임시 디버깅용입니다. 실제 운영 전에는 `false`로 되돌리세요.


## 14-6. 현재 발생한 오류(`rg: command not found`) 해결

이 오류는 서버에 `ripgrep(rg)`가 없을 때 발생할 수 있습니다.
최신 스크립트는 `rg`가 전혀 없어도 동작하며, 로그에서 URL을 `grep` 기반으로 파싱합니다.

```bash
cd /srv/app
git pull
sudo cp /srv/app/deploy/scripts/cloudflared-kv-updater.sh /srv/app/scripts/cloudflared-kv-updater.sh
sudo chmod +x /srv/app/scripts/cloudflared-kv-updater.sh
sudo cp /srv/app/deploy/systemd/work-time-cloudflared.service /etc/systemd/system/work-time-cloudflared.service

# 배포된 스크립트 버전 확인 (반드시 stream-simple-v9 이상이 보여야 함)
grep -n 'SCRIPT_VERSION' /srv/app/scripts/cloudflared-kv-updater.sh

# systemd가 실제 어떤 파일을 실행하는지 확인
sudo systemctl cat work-time-cloudflared.service

sudo systemctl daemon-reload
sudo systemctl reset-failed work-time-cloudflared.service
sudo systemctl restart work-time-cloudflared.service

journalctl -u work-time-cloudflared -n 120 --no-pager
sudo tail -n 120 /var/log/work-time/cloudflared.log
curl -fsS https://<worker-url>/_edge/status
```

로그에 `version=2026-02-11-stream-simple-v9`가 보여야 최신 스크립트가 실제로 실행된 것입니다.
`/_edge/status`에서 `has_active_url=true`가 나오면 Worker 리다이렉트가 정상 동작합니다.

## 14-7. 현재 발생한 오류(`429 Too Many Requests` / `host is not allowed`) 해결

### A) `Error unmarshaling QuickTunnel response ... status_code="429 Too Many Requests"`
Quick Tunnel 발급 요청이 Cloudflare 측에서 일시적으로 제한된 상태입니다.

```bash
# 서비스 재시작 폭주를 멈추고 잠시 대기
sudo systemctl stop work-time-cloudflared.service
sleep 120

# 재시작 후 로그 확인
sudo systemctl start work-time-cloudflared.service
journalctl -u work-time-cloudflared -n 150 --no-pager
```

최신 스크립트는 429 감지 시 백오프 재시도를 수행하고, systemd를 종료하지 않고 내부 루프로 냉각 후 재시도합니다(자체 self-heal).
최신 스크립트(`stream-simple-v9`)는 테스트 때처럼 **cloudflared stdout 스트림에서 URL을 즉시 파싱해 KV를 갱신**하는 단순 파이프라인 방식입니다(추가로 화이트리스트/검증 로직 포함).

### B) Worker에서 `active_url host is not allowed by whitelist`
KV에 들어간 URL 호스트와 Worker의 `ALLOWED_TUNNEL_HOSTS`가 맞지 않을 때 발생합니다.

Worker Variables를 아래처럼 설정하세요.
- `ALLOWED_TUNNEL_HOSTS=trycloudflare.com,cfargotunnel.com`

그리고 updater가 최신(`stream-simple-v9`)인지 확인 후 재시작합니다.

```bash
grep -n 'SCRIPT_VERSION' /srv/app/scripts/cloudflared-kv-updater.sh
sudo systemctl restart work-time-cloudflared.service
curl -fsS https://<worker-url>/_edge/status
```

`active_url_host`가 `trycloudflare.com` 또는 `cfargotunnel.com` 계열인지 확인하세요.

### C) 로그에 `curl: (22) ... 404`가 찍히는 원인
대부분 아래 중 하나입니다.
- `.env`의 `CF_ACCOUNT_ID` 또는 `CF_KV_NAMESPACE_ID`가 실제 Worker가 쓰는 KV와 다름
- `CF_API_TOKEN`이 해당 account/namespace에 대한 KV 쓰기 권한이 없음
- 예전 키(`ACTIVE_URL`) 또는 잘못된 key를 지우는 호출이 실패

`stream-simple-v9`부터는 오염 키 정리 시 404를 치명오류로 보지 않고 계속 진행하며, 필요하면 빈 값 PUT으로 대체합니다.

```bash
cd /srv/app
set -a; source /srv/app/.env; set +a

printf 'ACCOUNT=%s\nNAMESPACE=%s\n' "$CF_ACCOUNT_ID" "$CF_KV_NAMESPACE_ID"

curl -i -X GET \
  "https://api.cloudflare.com/client/v4/accounts/${CF_ACCOUNT_ID}/storage/kv/namespaces/${CF_KV_NAMESPACE_ID}/values/active_url" \
  -H "Authorization: Bearer ${CF_API_TOKEN}"
```

### D) Quick Tunnel 429가 계속돼 서비스가 안 올라오는 경우
Quick Tunnel(계정 없는 임시 터널)은 429가 길게 지속될 수 있습니다.

권장 대응(Quick Tunnel 유지):

```bash
# 1) 기존 updater 최신본 재배치
sudo install -m 0755 /srv/app/deploy/scripts/cloudflared-kv-updater.sh /srv/app/scripts/cloudflared-kv-updater.sh

# 2) systemd 재기동
sudo systemctl daemon-reload
sudo systemctl restart work-time-cloudflared.service

# 3) 최근 로그에서 429/URL 발급 상태 확인
journalctl -u work-time-cloudflared -n 120 --no-pager

# 4) edge 상태 확인
curl -fsS https://<worker-url>/_edge/status
```

- 429가 이어지면 `RATE_LIMIT_COOLDOWN_SECONDS`를 늘리고 재시작 빈도를 낮추세요.
- `CLEAR_KV_ON_429=false`로 stale URL 노출을 줄일 수 있습니다.

### E) UI는 뜨는데 `/api/health`가 400(Bad Request)인 경우
증상: UI는 보이는데 상태 표시가 빨간색이고 `/api/health`가 400 반복.
원인: FastAPI `TrustedHostMiddleware`가 현재 터널 호스트를 차단.

```bash
sudo sed -i 's#^TRUSTED_HOSTS=.*#TRUSTED_HOSTS=localhost,127.0.0.1,*.trycloudflare.com,*.cfargotunnel.com,*.workers.dev#' /srv/app/.env
sudo sed -i 's#^TRUST_ALL_HOSTS=.*#TRUST_ALL_HOSTS=false#' /srv/app/.env
sudo systemctl restart work-time-api
curl -i https://<현재-터널-호스트>/api/health
```

### F) 마스터 계정은 SQL에 자동 포함되나?
아닙니다. `MASTER_*`는 SQL seed가 아니라 런타임 부트스트랩 설정입니다.
최신 코드에서는 API 시작 시 `users` 테이블이 비어 있으면 `MASTER_*` 값으로 마스터 계정을 자동 생성합니다.

```bash
sudo systemctl restart work-time-api
psql "$DATABASE_URL" -c "select u.role, a.login_id from users u join auth_accounts a on a.user_id=u.id order by u.created_at;"
```

---

## 15. 부팅 자동실행/장애 자동복구 확인

```bash
systemctl is-enabled work-time-api
systemctl is-enabled work-time-cloudflared

# 재시작 테스트
sudo systemctl restart work-time-api
sudo systemctl restart work-time-cloudflared
```

두 서비스 모두 `Restart=always`로 설정되어 있습니다.

---

## 16. 최종 검증 시나리오

## 16-1. 서버 내부

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8080/health
curl -I http://127.0.0.1:8080/
```

## 16-2. 외부 사용자 관점

1. Worker URL 접속
2. 로그인 화면 확인
3. 로그인/목록 조회/요청 등록/관리자 기능 등 기존 기능 점검

---

## 17. 운영 중 자주 하는 작업

## 17-1. 코드 업데이트

```bash
cd /srv/app
git pull
source venv/bin/activate
pip install -r backend/requirements.txt
sudo systemctl restart work-time-api
sudo systemctl restart nginx
```

## 17-2. DB 마이그레이션 추가 적용

```bash
cd /srv/app
export DATABASE_URL='postgresql://worktime:***@127.0.0.1:5432/work_time'
for f in db/migrations/*.sql; do
  psql "$DATABASE_URL" -f "$f"
done
```

## 17-3. 장애 트러블슈팅 핵심 명령

```bash
journalctl -u work-time-api -f
journalctl -u work-time-cloudflared -f
sudo nginx -t
systemctl status nginx --no-pager
```

---

## 18. 보안 위협/현실적 대응

## 18-1. 서버 침해 및 시설망 lateral movement

- 위험: 서버 장악 시 내부망 이동 거점 가능
- 대응:
  - SSH는 Tailscale 경로만 허용
  - 로컬 포트(8000/8080) 외부 미개방
  - 최소권한 계정, 정기패치, 감사로그

## 18-2. JWT 탈취

- 위험: 브라우저 저장소 탈취/XSS
- 대응:
  - 입력 검증/출력 이스케이프
  - JWT 만료시간 관리
  - 관리자 계정 비번/권한 최소화

## 18-3. Worker/KV 변조

- 위험: `active_url` 악성 도메인 치환
- 대응:
  - Worker에서 `ALLOWED_TUNNEL_HOSTS` 강제
  - KV 토큰 최소권한 분리
  - 토큰 주기 교체

## 18-4. Quick Tunnel 구조 한계

- 한계: URL 가변, 고급 정책 제약
- 대응:
  - 사용자에게 Worker URL만 공지
  - cloudflared 서비스 상시 모니터링

---

## 19. 체크리스트

- [ ] Tailscale SSH 접속 성공
- [ ] `/srv/app/.env` 민감값 설정 완료
- [ ] DB schema + migrations 전체 적용
- [ ] `work-time-api`, `work-time-cloudflared`, `nginx` active
- [ ] Worker URL에서 UI/API 정상
- [ ] 재부팅 후 자동기동 확인


---

## 20. 빠른 트러블슈팅 (현재 질문 사례)

질문에 나온 아래 에러는 DB 문제라기보다 **Bash 히스토리 확장** 문제입니다.

- `-bash: !@127.0.0.1: event not found`

즉, 비밀번호 `Chamgo1234!`의 `!`를 셸이 이벤트 치환으로 해석한 것입니다.
다음 명령으로 바로 해결됩니다.

```bash
PGPASSWORD='Chamgo1234!' psql -h 127.0.0.1 -U worktime -d work_time -c 'SELECT 1;'
```

또는:

```bash
psql 'postgresql://worktime:Chamgo1234!@127.0.0.1:5432/work_time' -c 'SELECT 1;'
```


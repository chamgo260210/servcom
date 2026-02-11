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
3. Quick Tunnel URL은 재시작 시 바뀌므로, Worker가 KV의 `active_url`을 읽어 redirect 하도록 하면 사용자 진입 URL은 고정됩니다.

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
TRUSTED_HOSTS=localhost,127.0.0.1
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
TUNNEL_HOST_FILTER=trycloudflare.com
LOG_DIR=/var/log/work-time
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
   - `ALLOWED_TUNNEL_HOSTS=trycloudflare.com`
   - `BLOCK_DIRECT_API=true` (원하면)
5. Worker Settings > Bindings > KV Namespace 바인딩 추가:
   - Variable name: `TUNNEL_KV`
   - Namespace: `work-time-kv`
6. Deploy

배포 후 Worker URL 예시:
- `https://work-time-gateway.<subdomain>.workers.dev`

이 URL이 사용자 고정 진입 URL이 됩니다.

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
sudo mkdir -p /var/log/work-time
sudo chown -R www-data:www-data /var/log/work-time
```

## 14-3. systemd 등록

```bash
sudo cp /srv/app/deploy/systemd/work-time-cloudflared.service /etc/systemd/system/work-time-cloudflared.service
sudo systemctl daemon-reload
sudo systemctl enable --now work-time-cloudflared.service
```

## 14-4. 동작 확인

```bash
systemctl status work-time-cloudflared --no-pager
journalctl -u work-time-cloudflared -n 200 --no-pager
```

정상이라면 로그에 trycloudflare URL 파싱 및 KV 업데이트 메시지가 보입니다.

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


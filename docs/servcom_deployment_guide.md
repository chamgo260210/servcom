# 시설망 서버컴(Outbound-only) 배포 가이드

## 1) 전체 아키텍처 설명

### 왜 이 구조인가
- 시설망 특성(공인 IP 인바운드 차단, 포트포워딩 불가) 때문에 서버에서 **외부로 나가는 연결만 허용**되는 구조가 필요합니다.
- `cloudflared` Quick Tunnel은 서버가 Cloudflare로 outbound 연결을 유지하므로 인바운드 없이 외부 접근을 성립시킵니다.
- Quick Tunnel URL이 매번 바뀌므로, Worker + KV 조합으로 **고정 진입점(Worker URL)** 을 제공하고 실제 터널 URL만 KV에서 교체합니다.

### 컴포넌트 역할
1. **Nginx (127.0.0.1:8080)**
   - `/` 정적 프론트 제공
   - `/api/*` 를 FastAPI(127.0.0.1:8000)로 리버스 프록시
2. **FastAPI (127.0.0.1:8000)**
   - 기존 인증/JWT/권한/업무 API 처리
   - `/health` 제공
3. **PostgreSQL**
   - 운영 데이터 저장
4. **cloudflared Quick Tunnel**
   - Nginx로 트래픽 전달하는 외부 진입 터널 생성
5. **Cloudflare Worker + KV**
   - `active_url`(현재 trycloudflare URL)을 조회해 redirect
   - 허용 호스트 화이트리스트, 간단 Rate Limit

### 대안 비교
- 직접 공인IP/도메인 + TLS: 시설망 제약으로 불가
- 외부 VPS 리버스 프록시: 비용/운영 복잡도 증가
- Cloudflare Zero Trust 유료 고급 기능: 무료 플랜 목표와 상충
- 따라서 현재 요구조건(무료/도메인 없음/시설망)을 가장 만족하는 선택은 **Quick Tunnel + Worker + KV** 입니다.

---

## 2) 서버 디렉토리 최종 구조 예시

```text
/srv/app/
  ├─ backend/
  ├─ ui/
  ├─ venv/
  ├─ db/
  ├─ scripts/
  │   └─ cloudflared-kv-updater.sh
  └─ .env
```

---

## 3) 코드/설정 변경 핵심 요약

### A. 저장소 분석 결과
- 백엔드는 `backend/main.py`에서 라우터를 등록하며, 인증은 `/auth/login` JWT 발급 → `Authorization: Bearer` 방식입니다.
- 권한 계층은 `MASTER > OPERATOR > MEMBER` 입니다.
- 프론트 API 진입점은 `ui/js/api.js`의 `API_BASE_URL`을 사용하며, 기존에는 Render 고정 URL이었습니다.
- DB는 `db/schema.sql` + `db/migrations/*.sql` + 루트 `migrations/*.sql`가 혼재되어 있었습니다.

### B. 프론트엔드 변경
- `ui/js/api.js`에서 API 기본 URL을 `https://...onrender.com` 고정값에서 `/api` 상대 경로로 변경.
- 필요 시 `localStorage.api_base_url_override`로 임시 오버라이드 가능.
- 결과: Nginx 기준 동일 오리진 통신으로 CORS/mixed-content 위험 감소.

### C. 백엔드 변경
- `backend/app/config.py`
  - `API_ROOT_PATH`, `TRUSTED_HOSTS`, CORS env 처리 로직 추가.
- `backend/main.py`
  - `TrustedHostMiddleware` 적용.
  - CORS는 환경변수 기반으로만 활성화(기본 비활성).
  - `FastAPI(root_path=API_ROOT_PATH)`로 프록시 환경 대응.

### D. DB 스키마 정리
- 최종 스키마 기준으로 `db/schema.sql`을 정리:
  - `serial_layouts.walls JSONB`
  - `serial_shelf_types.color VARCHAR(7)`
  - `serial_publications.shelf_row_end/shelf_column_end`
- 루트 `migrations/*.sql`에서만 존재하던 변경을 정식 체인으로 편입하기 위해
  - `db/migrations/0013_serial_layout_and_shelf_type_extensions.sql` 추가.

### E. 자동화/인프라 템플릿 추가
- `deploy/nginx/work-time.conf`
- `deploy/systemd/work-time-api.service`
- `deploy/systemd/work-time-cloudflared.service`
- `deploy/scripts/cloudflared-kv-updater.sh`
- `deploy/cloudflare/worker.js`
- `deploy/.env.server.example`

---

## 4) 처음부터 재현 가능한 설치 가이드 (Ubuntu 22.04)

## 4-1. 코드 배치
```bash
sudo mkdir -p /srv/app
sudo chown -R $USER:$USER /srv/app
cd /srv/app
git clone <이-레포-URL> .
```

## 4-2. Python/venv
```bash
cd /srv/app
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r backend/requirements.txt
```

## 4-3. DB 스키마 적용
```bash
cd /srv/app
psql "$DATABASE_URL" -f db/schema.sql
for f in db/migrations/*.sql; do
  psql "$DATABASE_URL" -f "$f"
done
```

## 4-4. 환경변수(.env)
```bash
cd /srv/app
cp deploy/.env.server.example .env
# nano .env 로 DATABASE_URL, JWT_SECRET, CF_* 값 채우기
```

## 4-5. Nginx 적용
```bash
sudo cp deploy/nginx/work-time.conf /etc/nginx/sites-available/work-time
sudo ln -sf /etc/nginx/sites-available/work-time /etc/nginx/sites-enabled/work-time
sudo nginx -t
sudo systemctl restart nginx
```

## 4-6. cloudflared 설치(없으면)
```bash
which cloudflared || (curl -L https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb && sudo dpkg -i /tmp/cloudflared.deb)
```

## 4-7. 스크립트/서비스 파일 배치
```bash
sudo mkdir -p /srv/app/scripts
sudo cp deploy/scripts/cloudflared-kv-updater.sh /srv/app/scripts/cloudflared-kv-updater.sh
sudo chmod +x /srv/app/scripts/cloudflared-kv-updater.sh

sudo cp deploy/systemd/work-time-api.service /etc/systemd/system/work-time-api.service
sudo cp deploy/systemd/work-time-cloudflared.service /etc/systemd/system/work-time-cloudflared.service
sudo systemctl daemon-reload
sudo systemctl enable --now work-time-api.service
sudo systemctl enable --now work-time-cloudflared.service
```

## 4-8. 로그/상태 확인
```bash
systemctl status work-time-api --no-pager
systemctl status work-time-cloudflared --no-pager
journalctl -u work-time-api -n 200 --no-pager
journalctl -u work-time-cloudflared -n 200 --no-pager
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8080/health
```

## 4-9. Worker/KV 적용
1. KV namespace 생성 (`TUNNEL_KV`).
2. Worker 환경변수/바인딩:
   - KV binding 이름: `TUNNEL_KV`
   - `ALLOWED_TUNNEL_HOSTS=trycloudflare.com`
   - `BLOCK_DIRECT_API=true` (선택)
3. `deploy/cloudflare/worker.js` 배포.
4. 서버의 `work-time-cloudflared`가 KV `active_url`을 자동 갱신하는지 확인.

---

## 5) 보안 위협 분석 및 최소 대응

### 5-1. 서버컴 침투
- 공격: 취약한 SSH 계정/패스워드 재사용/패치 미흡.
- 위험도: 높음(시설망 내부 lateral movement 발판 가능).
- 최소 대응:
  - SSH 키 인증 강제, 비밀번호 로그인 비활성.
  - UFW 최소 오픈(22/tcp 내부 관리망만), 8080/8000 외부 미개방.
  - 정기 보안 업데이트, `fail2ban` 적용.

### 5-2. JWT 토큰 탈취
- 공격: XSS, 브라우저 저장소 탈취.
- 위험도: 중간.
- 최소 대응:
  - CSP 헤더 적용 검토, 입력값 출력 시 escape.
  - JWT 만료시간 단축, refresh 로직 유지.
  - 운영자 계정 주기적 비밀번호 교체.

### 5-3. Worker/KV 악용
- 공격: KV `active_url` 변조로 피싱/우회.
- 위험도: 중간~높음.
- 최소 대응:
  - API 토큰 최소권한(KV write만) 분리.
  - Worker에서 `ALLOWED_TUNNEL_HOSTS` 화이트리스트 강제.
  - Worker/KV 변경 감사(Cloudflare 로그) 주기 확인.

### 5-4. 프론트+백엔드 동일 서버
- 공격: 웹 취약점 하나로 전체 서비스 영향.
- 위험도: 중간.
- 최소 대응:
  - 프로세스 분리(systemd), least privilege 사용자(`www-data`).
  - DB 계정 권한 최소화(해당 DB만).
  - 백업/복구 절차 문서화.

### 5-5. Quick Tunnel 한계
- 특성: URL이 변동, 강한 enterprise 정책 부재.
- 위험도: 구조적 중간.
- 최소 대응:
  - Worker 고정 URL만 사용자에게 공지.
  - tunnel URL은 내부 비공개 취급.
  - 장애 시 서비스 재기동 절차(시스템 서비스 자동 재시작) 유지.

---

## 6) 운영 체크리스트
- [ ] `/health` 내부/외부 모두 정상
- [ ] Worker URL로 UI 접근 가능
- [ ] 로그인/권한별 화면/API 동작 정상
- [ ] DB 마이그레이션 최신(0013 포함)
- [ ] systemd 재부팅 후 자동기동 확인

# Cloudflare Named Tunnel 전환 런북 (무료 플랜 기준)

이 문서는 **Quick Tunnel(임시 URL)** 기반 운영에서 발생하는 429/만료/주소 변경 이슈를 줄이기 위해,
**Named Tunnel + 고정 호스트네임**으로 전환하는 절차를 단계별로 정리합니다.

---

## 0) 목표와 전제

### 목표

- 사용자 진입 주소는 기존 Worker 고정 도메인 유지
- 원본 터널 endpoint는 Named Tunnel로 고정 운영
- Quick Tunnel 429/만료에 따른 장애를 구조적으로 감소

### 전제

- 서버 앱은 `http://127.0.0.1:8080` 로컬에서 정상 응답
- Cloudflare 계정에 접근 가능
- 현재 저장소의 `deploy/scripts/cloudflared-kv-updater.sh`를 사용 중

---

## 1) 무료 플랜 범위에서의 현실적인 운영 가이드

1. **Named Tunnel 자체는 무료 플랜에서도 사용 가능**한 구간이 일반적입니다.
2. 다만 Cloudflare 요금/정책은 수시 변경될 수 있으므로,
   전환 직전에 대시보드의 Zero Trust/Workers 요금 안내를 다시 확인하세요.
3. 핵심은 비용보다 안정성입니다.
   - Quick Tunnel: 임시/제한 가능성 큼
   - Named Tunnel: 운영용으로 권장

> 운영 판단: “항상 무료 보장”보다 “현 시점 정책 확인 + Named Tunnel 기반 안정 운영”이 정답입니다.

---

## 2) Cloudflare 측 준비 (대시보드)

### 2-1. Named Tunnel 생성

Cloudflare Dashboard (Zero Trust)에서:

1. **Networks(or Access) > Tunnels** 이동
2. **Create a tunnel**
3. 타입은 `Cloudflared` 선택
4. 이름 예시: `work-time-prod`
5. 생성 후 **Tunnel Token** 발급/복사

### 2-2. Public Hostname 연결

생성한 tunnel에 public hostname을 연결합니다.

- 예시 호스트: `worktime-tunnel.<your-domain>`
- 서비스 URL: `http://127.0.0.1:8080`

> 이 단계가 완료되면 Cloudflare DNS에 터널 경로가 연결됩니다.

### 2-3. 방화벽/정책

- 서버 outbound 443이 Cloudflare로 나갈 수 있어야 함
- 내부 방화벽에서 불필요한 inbound 개방은 하지 않음

---

## 3) 서버 반영 (현재 스크립트 기준)

현재 스크립트는 이미 Named Tunnel 모드를 지원합니다.

- `CLOUDFLARED_TUNNEL_TOKEN` 설정 시 named tunnel 모드 진입
- `STATIC_TUNNEL_URL` 값을 KV에 유지하여 Worker가 참조

### 3-1. `.env` 설정

`/srv/app/.env`에 아래 추가/변경:

```dotenv
# Named Tunnel 전환
CLOUDFLARED_TUNNEL_TOKEN=<Cloudflare에서 발급한 토큰>
STATIC_TUNNEL_URL=https://worktime-tunnel.<your-domain>

# 기존 유지
KV_KEY=active_url
KV_UPDATED_AT_KEY=active_url_updated_at
TUNNEL_HOST_FILTER=trycloudflare.com,cfargotunnel.com,<your-domain>
TUNNEL_HOST_DENY=api.trycloudflare.com
CLEAR_KV_ON_429=true
```

중요:
- `STATIC_TUNNEL_URL`은 반드시 실제 public hostname과 일치해야 함
- `TUNNEL_HOST_FILTER`에 `<your-domain>`을 추가해야 host 검증에서 통과됨

### 3-2. 서비스 재시작

```bash
sudo systemctl daemon-reload
sudo systemctl restart work-time-cloudflared
```

### 3-3. 로그 확인

```bash
systemctl status work-time-cloudflared --no-pager
journalctl -u work-time-cloudflared -n 200 --no-pager
```

정상 기대 로그:
- `named tunnel mode enabled (token present).`
- `KV key 'active_url' updated and verified`

---

## 4) Worker 측 반영

Worker Variables 확인:

- `PROXY_TO_TUNNEL=true`
- `ALLOWED_TUNNEL_HOSTS=trycloudflare.com,cfargotunnel.com,<your-domain>`
- `DENIED_TUNNEL_HOSTS=api.trycloudflare.com`
- `MAX_ACTIVE_URL_AGE_SECONDS=1800`
- `ACTIVE_URL_UPDATED_AT_KEY=active_url_updated_at`

그리고 KV binding `TUNNEL_KV`가 연결되어 있어야 합니다.

---

## 5) 전환 검증 체크리스트

### 5-1. 로컬 앱

```bash
curl -i http://127.0.0.1:8080/health
```

### 5-2. Edge 상태

```bash
curl -s https://<worker-url>/_edge/status
```

확인:
- `has_active_url=true`
- `active_url_host`가 `<your-domain>` 또는 의도한 tunnel host
- `active_age_seconds`가 정상 범위

### 5-3. 외부 사용자 경로

```bash
curl -I https://<worker-url>/
```

- 5xx(530/1016)가 사라졌는지 확인

---

## 6) 장애 시 빠른 롤백

### A안: Named Tunnel 설정 재확인 후 재기동

```bash
sudo systemctl restart work-time-cloudflared
journalctl -u work-time-cloudflared -n 200 --no-pager
```

### B안: 임시 Quick Tunnel 복귀 (권장도 낮음)

`.env`에서 아래를 제거/주석:

- `CLOUDFLARED_TUNNEL_TOKEN`
- `STATIC_TUNNEL_URL`

그리고 재기동:

```bash
sudo systemctl restart work-time-cloudflared
```

> 단, Quick Tunnel 복귀 시 429/주소변경 문제는 다시 발생할 수 있습니다.

---

## 7) 운영 팁

1. 서버 재부팅 직후 자동 복구를 위해 systemd `enabled` 상태 유지
2. `_edge/status`를 모니터링 대상으로 등록
3. 429, KV mismatch, 530 빈도를 주간 단위로 집계
4. 가능하면 Named Tunnel hostname을 전용 서브도메인으로 분리


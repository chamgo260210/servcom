# Cloudflare 고정 도메인 → Tunnel 연결 장애 트러블슈팅

이 문서는 다음 증상을 빠르게 분리 진단하기 위한 절차입니다.

- 고정 Worker 도메인 접속 시 302가 발생하지만 실제 앱 연결 실패
- Cloudflare 530 / Error 1016 (Origin DNS error) 노출
- KV `active_url` 값이 갱신되는데도 간헐적으로 접속 실패
- 서버 로컬(`/health`)은 정상인데 외부 도메인은 실패

## 1) 이번 장애의 핵심 원인(요약)

1. **Quick Tunnel URL 추출 오인식**
   - `cloudflared` 로그에서 실제 임시 도메인(`*.trycloudflare.com`)이 아닌 제어용 호스트(`api.trycloudflare.com`)가 먼저 매칭되어 KV에 들어갈 수 있습니다.
   - 이 값으로 redirect/proxy하면 사용자 요청이 실제 터널로 가지 못합니다.

2. **서버 DNS 불안정으로 KV 업데이트 실패 반복 가능**
   - 서버 로그에 `Could not resolve host: api.cloudflare.com` 이 보이면 KV write/readback 검증이 실패하며, stale/empty KV로 이어집니다.

3. **엣지에서 stale URL을 계속 사용한 경우 530/1016 발생**
   - Quick Tunnel이 만료/종료되었는데 KV에 이전 URL이 남아 있으면 Cloudflare가 Origin DNS error(1016)를 반환합니다.

4. **엣지에서 redirect만 사용하는 구조의 취약성**
   - 클라이언트가 최종 `*.trycloudflare.com`에 직접 접속해야 하므로, 사용자 네트워크 정책/보안장비/브라우저 제약의 영향을 받습니다.

## 2) 스택별 단계 진단 절차

### A. 서버 애플리케이션 계층 (Nginx/FastAPI)

```bash
curl -i http://127.0.0.1:8080/health
curl -i http://127.0.0.1:8000/health
```

- 둘 다 200이면 앱 자체는 정상.
- 8080만 실패하면 Nginx 설정 우선 점검.
- 8000만 실패하면 FastAPI/systemd 로그 점검.

### B. cloudflared/systemd 계층

```bash
systemctl status work-time-cloudflared --no-pager
journalctl -u work-time-cloudflared -n 200 --no-pager
```

확인 포인트:
- `Discovered tunnel URL:`가 주기적으로 찍히는지
- `api.trycloudflare.com`이 잡히는지
- `429 Too Many Requests`가 있는지

### C. DNS/Cloudflare API 통신 계층

```bash
getent hosts api.cloudflare.com
curl -I https://api.cloudflare.com/client/v4/
```

- 여기서 실패하면 KV 갱신 실패는 구조적으로 반복됩니다.
- `/etc/resolv.conf`, 사내 DNS 정책, outbound 443 허용 여부를 우선 확인하세요.

### D. KV 값/Worker 상태 계층

```bash
curl -s https://<worker-domain>/_edge/status
```

확인 포인트:
- `has_active_url: true`
- `active_url_host`가 `api.trycloudflare.com`이 아닌 랜덤 하위도메인인지
- `proxy_mode` 값 확인

### E. 사용자 경로(브라우저) 계층

```bash
curl -I https://<worker-domain>/
```

- 302 redirect만 사용 시, `location`이 사용자의 네트워크에서 실제로 접근 가능한지 별도 검증 필요.
- proxy 모드에서는 Worker 도메인 고정으로 트래픽이 유지되어 클라이언트 제약 영향을 줄일 수 있습니다.

## 3) 재발성 판단

아래 조건이면 **재발 가능성이 높습니다**.

- Quick Tunnel(무상 임시 URL) 지속 사용
- 서버 DNS가 간헐 실패
- redirect 방식으로만 서비스
- `cloudflared` 재기동/네트워크 흔들림이 잦은 환경

## 4) 권장 해결책

### 즉시 적용 (단기)

1. KV 업데이트 스크립트에서 `api.trycloudflare.com` 차단(deny list) 적용
2. Worker에서 기본 동작을 redirect가 아니라 **proxy(fetch)** 로 전환
3. `_edge/status`로 운영자가 즉시 상태 확인 가능하게 유지

### 구조 개선 (중장기)

1. 서버 DNS 이중화(예: systemd-resolved + 신뢰 가능한 업스트림)
2. Quick Tunnel 재시도 간격/재시작 정책 최적화(429 완화)
3. 장애 대응을 위한 헬스체크/알람(Worker 상태, KV write 실패율, 429 빈도) 추가



### F. 530/1016 즉시 조치

```bash
# worker 상태
curl -s https://<worker-domain>/_edge/status

# 서버에서 tunnel 상태
systemctl status work-time-cloudflared --no-pager
journalctl -u work-time-cloudflared -n 120 --no-pager
```

- 429가 반복되면 Quick Tunnel 신규 발급 자체가 막힌 상태입니다.
- 이때는 `active_url`을 비우고(본 스크립트는 기본 자동 처리), cooldown 이후 재시도해야 합니다.
- 운영 중이면 재시작 빈도를 낮추고 429 cooldown 정책을 길게 잡으세요.

### G. KV `put()` 일일 한도 초과가 빠르게 발생하는 경우

원인 후보:
- Worker 요청마다 KV 기반 rate-limit 카운트를 쓰는 구성
- 429 구간에서 updater가 `active_url` 삭제/쓰기 루프를 반복

대응:
- Worker 변수 `EDGE_RATE_LIMIT_PER_MINUTE=0` (기본 비활성)
- updater `.env`에서 `CLEAR_KV_ON_429=false`로 write 최소화
- updater는 동일 URL이면 KV PUT을 생략(현재 스크립트 반영)


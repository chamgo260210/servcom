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
2. Worker는 KV read + redirect 전용으로 단순화하고, write 동작은 updater로만 제한
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
- 필요 시 `CLEAR_KV_ON_429=true`로 전환해 active_url 정리 정책을 사용할 수 있습니다(기본은 false).
- 운영 중이면 재시작 빈도를 낮추고 429 cooldown 정책을 길게 잡으세요.

### G. KV `put()` 일일 한도 초과가 빠르게 발생하는 경우

원인 후보:
- Worker 요청마다 KV 기반 rate-limit 카운트를 쓰는 구성
- 429 구간에서 updater가 `active_url` 삭제/쓰기 루프를 반복

대응:
- updater `.env`에서 `CLEAR_KV_ON_429=false`로 write 최소화
- updater `.env`에서 `SANITIZE_EXISTING_KV=false`로 주기적 KV 조회 최소화
- updater는 동일 URL이면 KV PUT을 생략(현재 스크립트 반영)

### H. KV vs D1 선택 기준 (사용량 최적화 관점)

- **권장 기본값:** KV 단일 저장소 + Worker 메모리 캐시(`ACTIVE_URL_CACHE_TTL_SECONDS`)
- D1 단독/병행은 가능하지만, 현재 시나리오(활성 URL 1개 저장)에서는 이점보다 복잡도가 커질 수 있습니다.
- write 최소화 목표라면 "URL 변경 시점에만 updater가 KV PUT" 구조가 가장 단순합니다.

### I. 트리거식 캐시 무효화(루프 조회 축소)

- Worker `ACTIVE_URL_CACHE_TTL_SECONDS`를 길게(예: 3600) 설정
- updater `.env`에 `WORKER_REFRESH_URL`, `WORKER_REFRESH_TOKEN` 설정
- URL이 실제 변경되어 KV가 갱신되면 updater가 `/_edge/refresh`를 호출
- 결과적으로 Worker의 KV 재조회는 TTL 만료 또는 캐시 무효화 시점에만 발생

### J. "Discovered URL host is not resolvable yet" 경고가 반복되는 경우

- Quick Tunnel 호스트는 발급 직후 DNS 전파 타이밍 이슈가 있을 수 있습니다.
- 기본값은 `REQUIRE_RESOLVABLE_TUNNEL_HOST=false`로 두는 것을 권장합니다.
- 이 값을 `true`로 두면 URL 갱신이 계속 skip되어 `active_url`이 비어 503이 날 수 있습니다.

### K. stale 정책 오탐 (`age exceeds max`)

- `active_url_updated_at`은 "URL이 바뀐 시점"입니다.
- Quick Tunnel URL이 몇 시간 유지되면, URL이 정상이어도 `MAX_ACTIVE_URL_AGE_SECONDS`만 초과해서 503이 날 수 있습니다.
- 저쓰기 운영(하루 1회 갱신 중심)에서는 `MAX_ACTIVE_URL_AGE_SECONDS=0`으로 비활성화하는 것을 권장합니다.

# 운영 업데이트: Named Tunnel 전환 점검/복구 가이드 (2026-03-18)

이 문서는 이번 이슈를 **한 문서에서** 빠르게 파악하기 위한 요약본입니다.

---

## 1) 현재 증상 요약

- Worker 응답: `503`
- 본문 메시지: `Tunnel origin DNS failed(530)`
- 의미: Worker가 바라보는 현재 `active_url` 호스트를 DNS로 해석하지 못함

즉, 앱(FastAPI/Nginx) 이전 단계에서 **터널 호스트 DNS 해석 실패**가 발생한 상태입니다.

---

## 2) 구조 도식 (현재 아키텍처)

```text
[사용자 브라우저]
   |
   v
[Cloudflare Worker 고정 URL]
   |  (KV에서 active_url 조회)
   v
[Named Tunnel Public Hostname]
   |  (DNS/CNAME로 cloudflared tunnel 라우팅)
   v
[cloudflared 프로세스 @ 서버]
   |
   v
[nginx :8080] -> [FastAPI :8000] -> [PostgreSQL]
```

핵심: Worker가 정상이어도, `Named Tunnel Public Hostname` DNS/CNAME이 잘못되면 530이 납니다.

---

## 3) 이번 상태에서 가장 먼저 확인할 것

### 3-1. `.env`의 `STATIC_TUNNEL_URL` 형식

- 반드시 **실제 공인 FQDN**이어야 합니다.
- 예: `https://worktime-tunnel.example.com`

주의:
- `https://worktime-tunnel.chamgo260210` 같은 값은 일반 공인 DNS 관점에서 해석 실패 가능성이 큽니다.
- Cloudflare DNS에 실제로 생성된 hostname과 **완전히 동일한 값**만 사용해야 합니다.

### 3-2. Cloudflare Tunnel Public Hostname 바인딩

대시보드에서 다음이 정확히 연결되어야 합니다.

1. Tunnel: `work-time-prod`(예시)
2. Public Hostname: `worktime-tunnel.<실제도메인>`
3. Service: `http://127.0.0.1:8080`

### 3-3. 서버 측 named tunnel 기동

- `CLOUDFLARED_TUNNEL_TOKEN` 설정
- `work-time-cloudflared` 서비스 재시작
- 로그에서 named tunnel mode 진입 확인

---

## 4) 즉시 점검 명령 (복붙용)

```bash
# 1) 서비스 상태
systemctl status work-time-cloudflared --no-pager

# 2) 최근 로그 (named mode / DNS 관련 에러 확인)
journalctl -u work-time-cloudflared -n 200 --no-pager

# 3) 서버에서 static tunnel host 해석 확인
getent hosts worktime-tunnel.<실제도메인>

# 4) Worker 상태 확인
curl -s https://<worker-url>/_edge/status

# 5) 외부 경로 확인
curl -i https://<worker-url>/
```

---

## 5) 이번 반영 내용(요약)

1. `cloudflared-kv-updater.sh`
   - Named Tunnel 모드 시작 전 `STATIC_TUNNEL_URL` 호스트 DNS 해석 가능 여부 사전 점검 추가
   - 해석 불가 시 즉시 에러와 힌트 출력

2. `worker.js`
   - 530 오류 메시지를 Named Tunnel DNS/CNAME 점검 중심으로 명확화

3. 본 문서 추가
   - 구조 도식 + 현재 장애 포인트 + 즉시 점검 명령을 한 문서에 통합

---

## 6) 앞으로 문서 제공 방식

요청하신 대로, 이후 운영 지시/설명은 다음 원칙으로 제공합니다.

- **신규 이슈/절차는 새 문서로 추가**
- 기존 문서는 링크만 보강(필수 수정만 최소 반영)
- “이번 변경 요약” 섹션을 신규 문서에 항상 포함


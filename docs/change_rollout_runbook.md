# 코드 변경 적용(배포) 런북

## 1) 배포 전 체크

```bash
git fetch --all --prune
git log --oneline --decorate -n 5
```

- 배포 대상 커밋/태그를 명확히 고정합니다.

## 2) 서버 반영 절차

```bash
cd /srv/app
git fetch --all --prune
git checkout <deploy-commit-or-tag>
```

백엔드 의존성 변경이 있으면:

```bash
source /srv/app/venv/bin/activate
pip install -r /srv/app/backend/requirements.txt
```

DB 변경이 있으면 마이그레이션 적용.

## 3) 서비스 재기동 순서(권장)

```bash
sudo systemctl daemon-reload
sudo systemctl restart work-time-api
sudo systemctl restart nginx
sudo systemctl restart work-time-cloudflared
```

## 4) 배포 직후 검증

```bash
curl -i http://127.0.0.1:8080/health
systemctl status work-time-cloudflared --no-pager
curl -s https://<worker-domain>/_edge/status
curl -I https://<worker-domain>/
```

검증 기준:
- 로컬 health 200
- cloudflared active(running)
- `_edge/status`에서 `has_active_url=true`
- 외부 접근 응답 정상(redirect 또는 proxy)

## 5) 롤백

```bash
cd /srv/app
git checkout <previous-known-good-commit>
sudo systemctl restart work-time-api nginx work-time-cloudflared
```

문제 재현 로그를 반드시 수집합니다.

```bash
journalctl -u work-time-cloudflared -n 200 --no-pager
journalctl -u work-time-api -n 200 --no-pager
```


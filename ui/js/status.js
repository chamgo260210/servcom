// File: /ui/js/status.js
import { API_BASE_URL } from './api.js';

function fetchWithTimeout(url, { timeoutMs = 4000, ...options } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  return fetch(url, { ...options, signal: controller.signal }).finally(() => clearTimeout(timer));
}

function setStatusState(el, text, state, detail) {
  if (!el) return;
  const textEl = el.querySelector('.status-text');
  if (textEl) textEl.textContent = text;
  else el.textContent = text;
  el.classList.remove('status-ok', 'status-bad', 'status-pending');
  if (state) el.classList.add(state);
  el.title = detail || '';
}

export async function checkDbStatus(el) {
  if (!el) return;
  setStatusState(el, 'DB 연결 확인 중...', 'status-pending');
  try {
    const resp = await fetchWithTimeout(`${API_BASE_URL}/health`, { cache: 'no-store' });
    if (!resp.ok) throw new Error('health_failed');
    const data = await resp.json();
    const ok = data.db_status === 'ok' || data.db === 'ok';
    setStatusState(el, ok ? 'DB 연결: 정상' : 'DB 연결: 확인 필요', ok ? 'status-ok' : 'status-bad');
  } catch (e) {
    setStatusState(el, 'DB 연결: 실패', 'status-bad');
  }
}

export async function checkSystemStatus(serverEl, dbEl, metaEl, options = {}) {
  const started = performance.now();
  const attempt = options.__attempt || 1;
  const timeoutMs = options.timeoutMs ?? 4000;
  const maxRetries = options.autoRetry ? (options.maxRetries ?? Infinity) : (options.maxRetries ?? 0);
  if (serverEl) setStatusState(serverEl, '서버 확인 중', 'status-pending');
  if (dbEl) setStatusState(dbEl, 'DB 확인 중', 'status-pending');
  if (metaEl) metaEl.textContent = '상태 체크 중...';
  try {
    const resp = await fetchWithTimeout(`${API_BASE_URL}/health`, { cache: 'no-store', timeoutMs });
    if (!resp.ok) throw new Error(`health_failed_${resp.status}`);
    const data = await resp.json();
    const latency = Math.max(1, Math.round(performance.now() - started));
    const checkedAt = new Date();
    const dbOk = data.db_status === 'ok' || data.db === 'ok';

    const detail = `응답 ${latency}ms · ${checkedAt.toLocaleTimeString()} 체크`;
    const tooltip = `서버: 정상 · DB: ${dbOk ? '정상' : '확인 필요'} · ${detail}`;
    setStatusState(serverEl, '서버', 'status-ok', tooltip);
    setStatusState(dbEl, dbOk ? 'DB' : 'DB(확인)', dbOk ? 'status-ok' : 'status-bad', tooltip);
    if (metaEl) metaEl.textContent = `최근 체크 ${checkedAt.toLocaleTimeString()} · ${latency}ms`;
    if (options.onRecover && attempt > 1) options.onRecover();
    return { ok: true, latency, checkedAt };
  } catch (e) {
    const reason = e?.message || '연결 오류';
    const nextAttempt = attempt + 1;
    const retryDelay = options.retryDelay ?? 1800;
    const detail = `오류: ${reason}${Number.isFinite(maxRetries) ? ` · ${attempt}/${maxRetries}회 시도` : ''}`;
    setStatusState(serverEl, '서버 오류', 'status-bad', detail);
    setStatusState(dbEl, 'DB 오류', 'status-bad', detail);
    if (metaEl) metaEl.textContent = detail;
    if (options.onRetry) options.onRetry(nextAttempt, maxRetries, detail);
    const shouldRetry = options.autoRetry && (nextAttempt <= maxRetries || !Number.isFinite(maxRetries));
    if (shouldRetry) {
      setTimeout(() => {
        checkSystemStatus(serverEl, dbEl, metaEl, { ...options, __attempt: nextAttempt });
      }, retryDelay);
    }
    return { ok: false, error: reason };
  }
}

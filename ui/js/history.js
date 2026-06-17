// File: /ui/js/history.js
import { apiRequest } from './api.js';
import { formatDateTimeSeoul } from './datetime.js';

const DEFAULT_DAYS = '30';
const DEFAULT_LIMIT = '50';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function formatCount(value) {
  return Number(value || 0).toLocaleString();
}

function formatOptionalDate(value) {
  return value ? formatDateTimeSeoul(value) : '-';
}

function currentFilters() {
  const days = document.getElementById('history-days')?.value || DEFAULT_DAYS;
  const limit = document.getElementById('history-limit')?.value || DEFAULT_LIMIT;
  const actionType = document.getElementById('history-action-type')?.value || '';
  return { days, limit, actionType };
}

function buildHistoryPath() {
  const { days, limit, actionType } = currentFilters();
  const params = new URLSearchParams({ days, limit });
  if (actionType) params.set('action_type', actionType);
  return `/history?${params.toString()}`;
}

function setStatText(id, label, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = `${label}: ${value}`;
}

function renderStats(stats) {
  setStatText('history-stat-total', '전체 저장 로그', `${formatCount(stats.total_logs)}건`);
  setStatText('history-stat-recent', '최근 30일 로그', `${formatCount(stats.recent_30_days)}건`);
  setStatText('history-stat-current', '현재 조회 결과', '0건');
  setStatText('history-stat-request-unlinked', 'request 연결 해제 로그', `${formatCount(stats.request_unlinked)}건`);
  setStatText('history-stat-actor-missing', 'actor 없는 로그', `${formatCount(stats.actor_missing)}건`);
  setStatText('history-stat-oldest', '가장 오래된 로그', formatOptionalDate(stats.oldest_log));
  setStatText('history-stat-newest', '가장 최신 로그', formatOptionalDate(stats.newest_log));

  const actionSelect = document.getElementById('history-action-type');
  if (!actionSelect) return;
  const currentValue = actionSelect.value;
  actionSelect.innerHTML = '';
  const allOption = document.createElement('option');
  allOption.value = '';
  allOption.textContent = '전체 action_type';
  actionSelect.appendChild(allOption);
  Object.entries(stats.by_action || {})
    .sort(([left], [right]) => left.localeCompare(right))
    .forEach(([actionType, count]) => {
      const option = document.createElement('option');
      option.value = actionType;
      option.textContent = `${actionType} (${formatCount(count)})`;
      actionSelect.appendChild(option);
    });
  actionSelect.value = currentValue;
}

async function loadHistoryStats() {
  const status = document.getElementById('history-stats-status');
  if (status) status.textContent = '통계를 불러오는 중...';
  try {
    const stats = await apiRequest('/history/stats');
    renderStats(stats);
    if (status) status.textContent = '통계 기준: 전체 audit_logs';
  } catch (e) {
    if (status) status.textContent = `통계를 불러오지 못했습니다: ${e.message || e}`;
  }
}

async function loadHistory(currentUser) {
  const tbody = document.querySelector('#history-table tbody');
  const status = document.getElementById('history-status');
  const list = document.getElementById('history-list');
  const { days, limit, actionType } = currentFilters();
  if (status) status.textContent = '이력을 불러오는 중...';
  if (!tbody && !list) return;
  if (tbody) tbody.innerHTML = '';
  if (list) list.innerHTML = '';
  try {
    const logs = await apiRequest(buildHistoryPath());
    setStatText('history-stat-current', '현재 조회 결과', `${formatCount(logs.length)}건`);
    if (!logs || !logs.length) {
      if (status) status.textContent = '선택한 조건에 해당하는 이력이 없습니다';
      return;
    }
    logs.forEach((log) => {
      const detail = log.details ? JSON.stringify(log.details, null, 2) : '';
      if (list) {
        const card = document.createElement('div');
        card.className = 'history-card';
        card.innerHTML = `
          <div class="history-line"><strong>${escapeHtml(log.action_label || log.action_type)}</strong></div>
          <div class="history-meta">${formatDateTimeSeoul(log.created_at)}</div>
          <div class="history-row">신청자: ${escapeHtml(log.actor_name || log.actor_user_id || '-')}</div>
          <div class="history-row">대상자: ${escapeHtml(log.target_name || log.target_user_id || '-')}</div>
          <div class="history-row">신청 ID: ${escapeHtml(log.request_id || '-')}</div>
          ${detail ? `<details class="history-detail"><summary>세부 보기</summary><pre>${escapeHtml(detail)}</pre></details>` : ''}
        `;
        list.appendChild(card);
        return;
      }
      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td>${formatDateTimeSeoul(log.created_at)}</td>
        <td>${escapeHtml(log.action_label || log.action_type)}</td>
        <td>${escapeHtml(log.actor_name || log.actor_user_id || '-')}</td>
        <td>${escapeHtml(log.target_name || log.target_user_id || '-')}</td>
        <td>${escapeHtml(log.request_id || '-')}</td>
        <td>${escapeHtml(detail)}</td>
      `;
      tbody.appendChild(tr);
    });
    const dayLabel = days === 'all' ? '전체 기간' : `최근 ${days}일`;
    const actionLabel = actionType ? ` · ${actionType}` : '';
    if (status) status.textContent = `${dayLabel}${actionLabel} · 최대 ${limit}건 중 ${logs.length}건 표시 중`;
  } catch (e) {
    if (status) status.textContent = `이력을 불러오지 못했습니다: ${e.message || e}`;
  }
}

function bindHistoryControls(currentUser) {
  ['history-days', 'history-limit', 'history-action-type'].forEach((id) => {
    document.getElementById(id)?.addEventListener('change', () => loadHistory(currentUser));
  });
  document.getElementById('history-refresh')?.addEventListener('click', () => loadHistory(currentUser));
}

async function initHistory(currentUser) {
  bindHistoryControls(currentUser);
  await loadHistoryStats();
  await loadHistory(currentUser);
}

export { initHistory, loadHistory };

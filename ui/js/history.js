// File: /ui/js/history.js
import { apiRequest } from './api.js';
import { formatDateTimeSeoul } from './datetime.js';

const DEFAULT_DAYS = '30';
const DEFAULT_LIMIT = '50';

let latestHistoryStats = null;

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

function formatCount(value) {
  return Number(value || 0).toLocaleString();
}

function formatOptionalDate(value) {
  return value ? formatDateTimeSeoul(value) : '-';
}

function formatAgeMinutes(value) {
  if (value === null || value === undefined) return '-';
  const minutes = Number(value);
  if (!Number.isFinite(minutes)) return '-';
  if (minutes < 60) return `${minutes.toLocaleString()}분 전`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours.toLocaleString()}시간 전`;
  const days = Math.floor(hours / 24);
  return `${days.toLocaleString()}일 전`;
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
  latestHistoryStats = stats;
  setStatText('history-stat-total', '전체 로그', `${formatCount(stats.total_logs)}건`);
  setStatText('history-stat-last-7', '최근 7일', `${formatCount(stats.logs_last_7_days)}건`);
  setStatText('history-stat-recent', '최근 30일', `${formatCount(stats.recent_30_days)}건`);
  setStatText('history-stat-last-90', '최근 90일', `${formatCount(stats.logs_last_90_days)}건`);
  setStatText('history-stat-action-types', '이력 유형 수', `${formatCount(stats.action_type_count)}개`);
  setStatText('history-stat-current', '현재 조회 결과', '0건');
  setStatText('history-stat-query-limit', '현재 조회 제한', `${formatCount(currentFilters().limit)}건`);
  setStatText('history-stat-request-unlinked', '신청 연결 없는 로그', `${formatCount(stats.request_unlinked)}건`);
  setStatText('history-stat-actor-missing', '실행자 정보 없는 로그', `${formatCount(stats.actor_missing)}건`);
  setStatText('history-stat-orphan-request', '끊어진 신청 연결 로그', `${formatCount(stats.orphan_request_logs)}건`);
  setStatText('history-stat-orphan-actor', '없는 실행자 로그', `${formatCount(stats.orphan_actor_logs)}건`);
  setStatText('history-stat-orphan-target', '없는 대상자 로그', `${formatCount(stats.orphan_target_logs)}건`);
  setStatText('history-stat-oldest', '최초 로그', formatOptionalDate(stats.oldest_log));
  setStatText('history-stat-oldest-age', '최초 로그 경과', stats.oldest_log_age_days === null || stats.oldest_log_age_days === undefined ? '-' : `${formatCount(stats.oldest_log_age_days)}일`);
  setStatText('history-stat-newest', '최신 로그', formatAgeMinutes(stats.newest_log_age_minutes));

  const actionSelect = document.getElementById('history-action-type');
  if (!actionSelect) return;
  const currentValue = actionSelect.value;
  actionSelect.innerHTML = '';
  const allOption = document.createElement('option');
  allOption.value = '';
  allOption.textContent = '전체 이력 유형';
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
    const response = await apiRequest(buildHistoryPath());
    const logs = Array.isArray(response) ? response : [];
    setStatText('history-stat-current', '현재 조회 결과', `${formatCount(logs.length)}건`);
    setStatText('history-stat-query-limit', '현재 조회 제한', `${formatCount(limit)}건`);
    if (!logs.length) {
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
    if (status) {
      const totalText = latestHistoryStats ? ` ※ 실제 저장 로그는 ${formatCount(latestHistoryStats.total_logs)}건` : '';
      status.textContent = `현재 조건 결과: ${formatCount(logs.length)}건 표시 중 · ${dayLabel}${actionLabel} · 현재 조회 조건으로는 최대 ${limit}건까지만 표시됨${totalText}`;
    }
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

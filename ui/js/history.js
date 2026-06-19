// File: /ui/js/history.js
import { apiRequest } from './api.js';
import { formatDateTimeSeoul } from './datetime.js';

const DEFAULT_DAYS = '30';
const DEFAULT_LIMIT = '50';
const DEFAULT_EXPORT_LIMIT = '500';

let latestHistoryStats = null;

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
  const includeBeforeReset = Boolean(document.getElementById('history-include-before-reset')?.checked);
  return { days, limit, actionType, includeBeforeReset };
}

function buildHistoryPath() {
  const { days, limit, actionType, includeBeforeReset } = currentFilters();
  const params = new URLSearchParams({ days, limit });
  if (actionType) params.set('action_type', actionType);
  if (includeBeforeReset) params.set('include_before_reset', 'true');
  return `/history?${params.toString()}`;
}

function buildExportPath(format) {
  const { days, actionType, includeBeforeReset } = currentFilters();
  const exportLimit = document.getElementById('history-export-limit')?.value || DEFAULT_EXPORT_LIMIT;
  const params = new URLSearchParams({ days, limit: exportLimit, format });
  if (actionType) params.set('action_type', actionType);
  if (includeBeforeReset) params.set('include_before_reset', 'true');
  return `/history/export?${params.toString()}`;
}

function setStatText(id, label, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = `${label}: ${value}`;
}

function appendText(parent, tagName, text, className) {
  const el = document.createElement(tagName);
  if (className) el.className = className;
  el.textContent = text;
  parent.appendChild(el);
  return el;
}

function appendTableCell(row, text) {
  appendText(row, 'td', text ?? '-');
}

function appendDetail(parent, detail) {
  if (!detail) return;
  const details = document.createElement('details');
  details.className = 'history-detail';
  appendText(details, 'summary', '내역 보기');
  appendText(details, 'pre', detail);
  parent.appendChild(details);
}

function filenameFromDisposition(disposition, fallback) {
  const match = /filename="?([^"]+)"?/i.exec(disposition || '');
  return match?.[1] || fallback;
}

async function exportHistory(format) {
  const status = document.getElementById('history-export-status');
  if (status) status.textContent = `${format.toUpperCase()} 내보내기를 준비하는 중...`;
  try {
    const response = await apiRequest(buildExportPath(format), { responseType: 'blob' });
    const blob = await response.blob();
    const fallbackName = `audit_history.${format === 'json' ? 'json' : 'csv'}`;
    const filename = filenameFromDisposition(response.headers.get('Content-Disposition'), fallbackName);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    if (status) status.textContent = `${format.toUpperCase()} 내보내기를 완료했습니다.`;
  } catch (e) {
    if (status) status.textContent = `내보내기에 실패했습니다: ${e.message || e}`;
  }
}

function renderStats(stats) {
  latestHistoryStats = stats;
  setStatText('history-stat-total', '전체 로그', `${formatCount(stats.total_logs)}건`);
  setStatText(
    'history-stat-latest-reset',
    '최근 전체 초기화',
    stats.latest_full_reset_at ? formatOptionalDate(stats.latest_full_reset_at) : '없음',
  );
  setStatText(
    'history-stat-after-reset',
    '초기화 이후 로그',
    stats.latest_full_reset_at ? `${formatCount(stats.logs_after_full_reset)}건` : '전체 기준',
  );
  setStatText('history-stat-default-scope', '기본 조회 범위', stats.default_scope_label || '전체 기준');
  setStatText('history-stat-last-7', '최근 7일', `${formatCount(stats.logs_last_7_days)}건`);
  setStatText('history-stat-recent', '최근 30일', `${formatCount(stats.recent_30_days)}건`);
  setStatText('history-stat-last-90', '최근 90일', `${formatCount(stats.logs_last_90_days)}건`);
  setStatText('history-stat-action-types', '이력 유형 수', `${formatCount(stats.action_type_count)}개`);
  setStatText('history-stat-current', '현재 조회 결과', '0건');
  setStatText('history-stat-query-limit', '현재 조회 제한', `${formatCount(currentFilters().limit)}건`);
  setStatText('history-stat-request-unlinked', '신청 연결 없는 로그', `${formatCount(stats.request_unlinked)}건`);
  setStatText('history-stat-actor-missing', '수행자 정보 없는 로그', `${formatCount(stats.actor_missing)}건`);
  setStatText('history-stat-orphan-request', '끊어진 신청 연결 로그', '-');
  setStatText('history-stat-orphan-actor', '없는 수행자 로그', '-');
  setStatText('history-stat-orphan-target', '없는 대상자 로그', '-');
  setStatText('history-stat-oldest', '최초 로그', formatOptionalDate(stats.oldest_log));
  setStatText(
    'history-stat-oldest-age',
    '최초 로그 경과',
    stats.oldest_log_age_days === null || stats.oldest_log_age_days === undefined
      ? '-'
      : `${formatCount(stats.oldest_log_age_days)}일`,
  );
  setStatText('history-stat-newest', '최신 로그', formatAgeMinutes(stats.newest_log_age_minutes));
  const diagnosticsStatus = document.getElementById('history-diagnostics-status');
  if (diagnosticsStatus) diagnosticsStatus.textContent = '진단 전';

  const actionSelect = document.getElementById('history-action-type');
  if (!actionSelect) return;
  const currentValue = actionSelect.value;
  actionSelect.replaceChildren();
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
    if (status) status.textContent = `통계 기준: 전체 audit_logs / 기본 조회: ${stats.default_scope_label || '전체 기준'}`;
  } catch (e) {
    if (status) status.textContent = `통계를 불러오지 못했습니다. ${e.message || e}`;
  }
}

async function loadHistoryDiagnostics() {
  const button = document.getElementById('history-diagnostics-run');
  const status = document.getElementById('history-diagnostics-status');
  if (button) button.disabled = true;
  if (status) status.textContent = '진단 중...';
  try {
    const diagnostics = await apiRequest('/history/diagnostics');
    setStatText('history-stat-orphan-request', '끊어진 신청 연결 로그', `${formatCount(diagnostics.orphan_request_logs)}건`);
    setStatText('history-stat-orphan-actor', '없는 수행자 로그', `${formatCount(diagnostics.orphan_actor_logs)}건`);
    setStatText('history-stat-orphan-target', '없는 대상자 로그', `${formatCount(diagnostics.orphan_target_logs)}건`);
    if (status) status.textContent = `진단 완료: ${formatOptionalDate(diagnostics.checked_at)}`;
  } catch (e) {
    if (status) status.textContent = `연결 진단을 불러오지 못했습니다. ${e.message || e}`;
  } finally {
    if (button) button.disabled = false;
  }
}

async function loadHistory(currentUser) {
  const tbody = document.querySelector('#history-table tbody');
  const status = document.getElementById('history-status');
  const list = document.getElementById('history-list');
  const { days, limit, actionType, includeBeforeReset } = currentFilters();
  if (status) status.textContent = '이력을 불러오는 중...';
  if (!tbody && !list) return;
  if (tbody) tbody.replaceChildren();
  if (list) list.replaceChildren();
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
      const actorText = log.actor_display_name || log.actor_name || log.actor_user_id || '알 수 없음';
      const targetText = log.target_display_name || log.target_name || log.target_user_id || '-';
      const requestText = log.request_display_text || log.request_id || '-';
      const summaryText = log.details_summary || detail;

      if (list) {
        const card = document.createElement('div');
        card.className = 'history-card';
        const line = appendText(card, 'div', '', 'history-line');
        appendText(line, 'strong', log.action_label || log.action_type);
        appendText(card, 'div', formatDateTimeSeoul(log.created_at), 'history-meta');
        appendText(card, 'div', `수행자: ${actorText}`, 'history-row');
        appendText(card, 'div', `대상자: ${targetText}`, 'history-row');
        appendText(card, 'div', `신청: ${requestText}`, 'history-row');
        if (log.details_summary) appendText(card, 'div', `요약: ${log.details_summary}`, 'history-row');
        appendDetail(card, detail);
        list.appendChild(card);
        return;
      }

      const tr = document.createElement('tr');
      appendTableCell(tr, formatDateTimeSeoul(log.created_at));
      appendTableCell(tr, log.action_label || log.action_type);
      appendTableCell(tr, actorText);
      appendTableCell(tr, targetText);
      appendTableCell(tr, requestText);
      appendTableCell(tr, summaryText);
      tbody.appendChild(tr);
    });
    const dayLabel = days === 'all' ? '전체 기간' : `최근 ${days}일`;
    const actionLabel = actionType ? ` / ${actionType}` : '';
    const scopeLabel = includeBeforeReset
      ? '초기화 이전 포함'
      : latestHistoryStats?.default_scope_label || '기본 범위';
    if (status) {
      const totalText = latestHistoryStats
        ? ` / 전체 저장 로그 ${formatCount(latestHistoryStats.total_logs)}건`
        : '';
      status.textContent = `현재 조건 결과: ${formatCount(logs.length)}건 표시 중 / ${scopeLabel} / ${dayLabel}${actionLabel} / 최대 ${limit}건 표시${totalText}`;
    }
  } catch (e) {
    if (status) status.textContent = `이력을 불러오지 못했습니다. ${e.message || e}`;
  }
}

function bindHistoryControls(currentUser) {
  ['history-days', 'history-limit', 'history-action-type', 'history-include-before-reset'].forEach((id) => {
    document.getElementById(id)?.addEventListener('change', () => loadHistory(currentUser));
  });
  document.getElementById('history-refresh')?.addEventListener('click', () => loadHistory(currentUser));
  document.getElementById('history-diagnostics-run')?.addEventListener('click', () => loadHistoryDiagnostics());
  document.getElementById('history-export-csv')?.addEventListener('click', () => exportHistory('csv'));
  document.getElementById('history-export-json')?.addEventListener('click', () => exportHistory('json'));
}

async function initHistory(currentUser) {
  bindHistoryControls(currentUser);
  await loadHistoryStats();
  await loadHistory(currentUser);
}

export { initHistory, loadHistory };

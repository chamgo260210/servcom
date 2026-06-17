// File: /ui/js/history.js
import { apiRequest } from './api.js';
import { formatDateTimeSeoul } from './datetime.js';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

async function loadHistory(currentUser) {
  const tbody = document.querySelector('#history-table tbody');
  const status = document.getElementById('history-status');
  const list = document.getElementById('history-list');
  if (status) status.textContent = '이력을 불러오는 중...';
  if (!tbody && !list) return;
  if (tbody) tbody.innerHTML = '';
  if (list) list.innerHTML = '';
  try {
    const logs = await apiRequest('/history');
    if (!logs || !logs.length) {
      if (status) status.textContent = '최근 이력이 없습니다';
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
    if (status) status.textContent = `총 ${logs.length}건 표시 중`;
  } catch (e) {
    if (status) status.textContent = `이력을 불러오지 못했습니다: ${e.message}`;
  }
}

export { loadHistory };

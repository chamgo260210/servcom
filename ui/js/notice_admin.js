import { apiRequest } from './api.js';
import { getNoticeTypeLabel, formatNoticeDate } from './notices.js';
import { initAppLayout } from './layout.js';

const form = document.getElementById('notice-form');
const listEl = document.getElementById('notice-admin-list');
const emptyEl = document.getElementById('notice-admin-empty');
const scopeSelect = document.getElementById('notice-scope');
const roleTargets = document.getElementById('notice-role-targets');
const userTargets = document.getElementById('notice-user-targets');
const submitBtn = document.getElementById('notice-submit');
const cancelEditBtn = document.getElementById('notice-cancel-edit');

let usersCache = [];
let editingId = null;

const currentUser = await initAppLayout('notice_admin');

async function loadUsers() {
  try {
    usersCache = await apiRequest('/users');
  } catch (e) {
    console.warn('사용자 목록 로드 실패', e);
  }
}

function buildRoleOptions() {
  if (!roleTargets) return;
  const roles = currentUser?.role === 'OPERATOR'
    ? ['OPERATOR', 'MEMBER']
    : ['MASTER', 'OPERATOR', 'MEMBER'];
  roleTargets.innerHTML = roles
    .map((role) => `<label><input type="checkbox" value="${role}" /> ${role}</label>`)
    .join('');
}

function restrictTypeOptions() {
  if (!form || !currentUser) return;
  if (currentUser.role !== 'OPERATOR') return;
  const select = form.querySelector('select[name="type"]');
  if (!select) return;
  Array.from(select.options).forEach((option) => {
    if (['DB_MAINTENANCE', 'SYSTEM_MAINTENANCE'].includes(option.value)) {
      option.disabled = true;
    }
  });
  if (['DB_MAINTENANCE', 'SYSTEM_MAINTENANCE'].includes(select.value)) {
    select.value = 'WORK_SPECIAL';
  }
}

function buildUserOptions() {
  if (!userTargets) return;
  if (!usersCache.length) {
    userTargets.innerHTML = '<div class="muted small">사용자 목록을 불러올 수 없습니다.</div>';
    return;
  }
  const filtered = currentUser?.role === 'OPERATOR'
    ? usersCache.filter((user) => user.role === 'MEMBER' && user.id !== currentUser?.id)
    : usersCache.filter((user) => user.id !== currentUser?.id);
  userTargets.innerHTML = filtered
    .map((user) => `<label><input type="checkbox" value="${user.id}" /> ${user.name} (${user.role})</label>`)
    .join('');
}

function setScopeVisibility() {
  const scope = scopeSelect?.value || 'ALL';
  if (roleTargets) roleTargets.style.display = scope === 'ROLE' ? 'grid' : 'none';
  if (userTargets) userTargets.style.display = scope === 'USER' ? 'grid' : 'none';
}

function getSelectedValues(container) {
  if (!container) return [];
  return Array.from(container.querySelectorAll('input[type="checkbox"]:checked')).map((input) => input.value);
}

function resetForm() {
  if (!form) return;
  form.reset();
  editingId = null;
  if (submitBtn) submitBtn.textContent = '공지 등록';
  if (cancelEditBtn) cancelEditBtn.style.display = 'none';
  setScopeVisibility();
}

async function loadNotices() {
  if (listEl) listEl.innerHTML = '<div class="loader">공지사항을 불러오는 중...</div>';
  try {
    const notices = await apiRequest('/notices?include_inactive=true&include_all=true');
    renderNotices(notices);
  } catch (e) {
    if (listEl) listEl.innerHTML = `<div class="error">공지사항을 불러오지 못했습니다: ${e.message || e}</div>`;
  }
}

function renderNotices(notices) {
  if (!listEl) return;
  listEl.innerHTML = '';
  if (!notices.length) {
    if (emptyEl) emptyEl.style.display = 'block';
    return;
  }
  if (emptyEl) emptyEl.style.display = 'none';
  const channelLabels = {
    POPUP: '팝업',
    BANNER: '배너',
    POPUP_BANNER: '팝업+배너',
    NONE: '추가 노출 없음',
    BOARD: '추가 노출 없음'
  };
  notices.forEach((notice) => {
    const isMasterNotice = notice.creator_role === 'MASTER';
    const isOperator = currentUser?.role === 'OPERATOR';
    const disableActions = isOperator && isMasterNotice;
    const row = document.createElement('div');
    row.className = 'notice-admin-row';
    row.innerHTML = `
      <div>
        <div class="notice-admin-title">
          <span class="notice-tag">${getNoticeTypeLabel(notice.type)}</span>
          <strong>${notice.title}</strong>
          ${notice.is_active ? '' : '<span class="notice-status muted">비활성</span>'}
        </div>
        <div class="muted small">채널: ${channelLabels[notice.channel] || notice.channel} · 범위: ${notice.scope} · 시작: ${notice.start_at ? formatNoticeDate(notice.start_at) : '즉시'}</div>
      </div>
      <div class="notice-admin-actions">
        <button class="btn tiny" data-action="edit" ${disableActions ? 'disabled' : ''}>수정</button>
        <button class="btn tiny secondary" data-action="toggle" ${disableActions ? 'disabled' : ''}>${notice.is_active ? '비활성화' : '활성화'}</button>
        <button class="btn tiny danger" data-action="delete" ${disableActions ? 'disabled' : ''}>삭제</button>
      </div>
    `;
    row.querySelector('[data-action="edit"]').addEventListener('click', () => {
      if (!disableActions) populateForm(notice);
    });
    row.querySelector('[data-action="toggle"]').addEventListener('click', () => {
      if (!disableActions) toggleNotice(notice);
    });
    row.querySelector('[data-action="delete"]').addEventListener('click', () => {
      if (!disableActions) deleteNotice(notice);
    });
    listEl.appendChild(row);
  });
}

function populateForm(notice) {
  if (!form) return;
  editingId = notice.id;
  form.title.value = notice.title;
  form.body.value = notice.body;
  form.type.value = notice.type;
  const channels = {
    POPUP: ['popup'],
    BANNER: ['banner'],
    POPUP_BANNER: ['popup', 'banner'],
    NONE: [],
    BOARD: []
  };
  const selected = channels[notice.channel] || [];
  form.querySelectorAll('input[name="channels"]').forEach((input) => {
    input.checked = selected.includes(input.value);
  });
  form.scope.value = notice.scope;
  form.priority.value = notice.priority ?? 0;
  form.is_active.checked = notice.is_active;
  form.start_at.value = notice.start_at ? notice.start_at.slice(0, 16) : '';
  form.end_at.value = notice.end_at ? notice.end_at.slice(0, 16) : '';

  buildRoleOptions();
  buildUserOptions();
  setScopeVisibility();
  if (notice.target_roles && roleTargets) {
    notice.target_roles.forEach((role) => {
      const input = roleTargets.querySelector(`input[value="${role}"]`);
      if (input) input.checked = true;
    });
  }
  if (notice.target_user_ids && userTargets) {
    notice.target_user_ids.forEach((userId) => {
      const input = userTargets.querySelector(`input[value="${userId}"]`);
      if (input) input.checked = true;
    });
  }

  if (submitBtn) submitBtn.textContent = '공지 수정';
  if (cancelEditBtn) cancelEditBtn.style.display = 'inline-flex';
}

async function toggleNotice(notice) {
  await apiRequest(`/notices/${notice.id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_active: !notice.is_active })
  });
  loadNotices();
}

async function deleteNotice(notice) {
  if (!confirm('정말 삭제하시겠습니까?')) return;
  await apiRequest(`/notices/${notice.id}`, { method: 'DELETE' });
  loadNotices();
}

async function submitForm(event) {
  event.preventDefault();
  if (!form) return;
  const selectedChannels = Array.from(form.querySelectorAll('input[name="channels"]:checked')).map((input) => input.value);
  const channelValue = selectedChannels.includes('popup') && selectedChannels.includes('banner')
    ? 'POPUP_BANNER'
    : selectedChannels.includes('popup')
      ? 'POPUP'
      : selectedChannels.includes('banner')
        ? 'BANNER'
        : 'NONE';

  const payload = {
    title: form.title.value.trim(),
    body: form.body.value.trim(),
    type: form.type.value,
    channel: channelValue,
    scope: form.scope.value,
    priority: Number(form.priority.value || 0),
    is_active: form.is_active.checked
  };
  const operatorAllScope = currentUser?.role === 'OPERATOR' && payload.scope === 'ALL';
  if (operatorAllScope) {
    payload.scope = 'ROLE';
    payload.target_roles = ['OPERATOR', 'MEMBER'];
  }
  if (form.start_at.value) payload.start_at = new Date(form.start_at.value).toISOString();
  if (form.end_at.value) payload.end_at = new Date(form.end_at.value).toISOString();
  if (payload.scope === 'ROLE' && !operatorAllScope) {
    payload.target_roles = getSelectedValues(roleTargets);
  }
  if (payload.scope === 'USER') {
    payload.target_user_ids = getSelectedValues(userTargets);
  }

  const options = {
    method: editingId ? 'PATCH' : 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload)
  };
  const url = editingId ? `/notices/${editingId}` : '/notices';
  await apiRequest(url, options);
  resetForm();
  await loadNotices();
}

scopeSelect?.addEventListener('change', setScopeVisibility);
form?.addEventListener('submit', submitForm);
cancelEditBtn?.addEventListener('click', (event) => {
  event.preventDefault();
  resetForm();
});

await loadUsers();
buildRoleOptions();
buildUserOptions();
setScopeVisibility();
restrictTypeOptions();
loadNotices();

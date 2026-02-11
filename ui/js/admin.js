// File: /ui/js/admin.js
import { apiRequest } from './api.js';

const roleLabel = {
  MASTER: '마스터',
  OPERATOR: '운영자',
  MEMBER: '구성원'
};

let members = [];
let selectedMember = null;
let currentUserInfo = null;
const editorOptions = { allowCredentialEdit: false };
const assignGridCells = new Map();
const assignedSlots = new Set();
const selectedAssignSlots = new Set();
const days = ['월', '화', '수', '목', '금'];
const hours = Array.from({ length: 9 }, (_, i) => 9 + i); // 09~18시
const appliedRangeEl = () => document.getElementById('assign-current-range');
const manualDateOverride = { from: false, to: false };
let lastAssignUserId = null;

function parseDateValue(dateStr) {
  if (!dateStr) return new Date();
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

function formatDateOnlyLocal(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function weekStart(dateStr) {
  const d = parseDateValue(dateStr);
  const diff = (d.getDay() + 6) % 7;
  const start = new Date(d);
  start.setDate(d.getDate() - diff);
  return formatDateOnlyLocal(start);
}

function formatDateTimeLocal(dateStr) {
  if (!dateStr) return '-';
  try {
    return new Date(dateStr).toLocaleString();
  } catch {
    return dateStr;
  }
}

// ---------------------- 공통 유틸 ----------------------
function setButtonLoading(btn, isLoading, text) {
  if (!btn) return;
  btn.disabled = isLoading;
  btn.classList.toggle('loading', isLoading);
  if (text) btn.textContent = text;
}

function updateSaveButtonState() {
  const saveBtn = document.getElementById('edit-save');
  if (!saveBtn) return;
  const name = document.getElementById('edit-name')?.value.trim();
  const login = document.getElementById('edit-login')?.value.trim();
  const password = document.getElementById('edit-password')?.value.trim();
  const isEditing = Boolean(selectedMember);
  const passwordReady = isEditing ? true : Boolean(password);
  saveBtn.disabled = !(name && login && passwordReady);
}

function setEditForm(member) {
  if (selectedMember?.id === member.id) {
    clearEditForm();
    return;
  }
  selectedMember = member;
  const idInput = document.getElementById('edit-id');
  const nameInput = document.getElementById('edit-name');
  const identInput = document.getElementById('edit-identifier');
  const roleSelect = document.getElementById('edit-role');
  const activeSelect = document.getElementById('edit-active');
  const loginInput = document.getElementById('edit-login');
  const pwInput = document.getElementById('edit-password');
  const saveBtn = document.getElementById('edit-save');
  if (idInput) idInput.value = member.id;
  if (nameInput) nameInput.value = member.name || '';
  if (identInput) identInput.value = member.identifier || '';
  if (roleSelect) roleSelect.value = member.role;
  if (activeSelect) activeSelect.value = member.active ? 'true' : 'false';
  if (loginInput) loginInput.value = member.auth_account?.login_id || '';
  if (pwInput) {
    pwInput.value = '';
    pwInput.disabled = !editorOptions.allowCredentialEdit;
  }
  if (saveBtn) saveBtn.disabled = false;
  const detail = document.getElementById('member-detail');
  const modeLabel = document.getElementById('member-form-mode');
  if (modeLabel) modeLabel.textContent = `${member.name} 선택됨`;
  if (detail) {
    const lastLogin = member.auth_account?.last_login_at ? new Date(member.auth_account.last_login_at).toLocaleString() : '기록 없음';
    detail.innerHTML = `
      <strong>선택됨:</strong> ${member.name} / ${roleLabel[member.role] || member.role}<br />
      개인 ID: ${member.identifier || '-'} · 로그인 ID: ${member.auth_account?.login_id || '-'}<br />
      상태: ${member.active ? '활성' : '비활성'} · 최근 로그인: ${lastLogin}<br />
      비밀번호는 해시로 저장되어 조회할 수 없습니다. 필요 시 새 임시 비밀번호로 재설정해 전달하세요.
    `;
  }
  renderMembers();
  updateSaveButtonState();
}

function clearEditForm() {
  selectedMember = null;
  ['edit-id', 'edit-name', 'edit-identifier'].forEach((id) => {
    const el = document.getElementById(id);
    if (el) el.value = '';
  });
  const roleSelect = document.getElementById('edit-role');
  const activeSelect = document.getElementById('edit-active');
  const saveBtn = document.getElementById('edit-save');
  const loginInput = document.getElementById('edit-login');
  const pwInput = document.getElementById('edit-password');
  if (roleSelect) roleSelect.value = 'MEMBER';
  if (activeSelect) activeSelect.value = 'true';
  if (loginInput) loginInput.value = '';
  if (pwInput) {
    pwInput.value = '';
    pwInput.disabled = !editorOptions.allowCredentialEdit;
  }
  updateSaveButtonState();
  const detail = document.getElementById('member-detail');
  const modeLabel = document.getElementById('member-form-mode');
  if (modeLabel) modeLabel.textContent = '신규 구성원을 추가하거나 목록에서 선택해 수정하세요.';
  if (detail) detail.textContent = '테이블에서 구성원을 선택하면 상세 정보가 표시됩니다.';
  renderMembers();
}

function applyMemberFilters(list) {
  const keyword = (document.getElementById('member-search')?.value || '').toLowerCase();
  const roleFilter = document.getElementById('member-filter-role')?.value || '';
  const activeFilter = document.getElementById('member-filter-active')?.value || '';
  return list.filter((m) => {
    const matchesKeyword =
      !keyword ||
      m.name.toLowerCase().includes(keyword) ||
      (m.identifier || '').toLowerCase().includes(keyword) ||
      (m.auth_account?.login_id || '').toLowerCase().includes(keyword);
    const matchesRole = !roleFilter || m.role === roleFilter;
    const matchesActive =
      !activeFilter ||
      (activeFilter === 'active' && m.active) ||
      (activeFilter === 'inactive' && !m.active);
    return matchesKeyword && matchesRole && matchesActive;
  });
}

function renderMembers() {
  const tbody = document.getElementById('member-table-body');
  const cardList = document.getElementById('member-card-list');
  const count = document.getElementById('member-count');
  if (!tbody && !cardList) return;
  if (tbody) tbody.innerHTML = '';
  if (cardList) cardList.innerHTML = '';
  const filtered = applyMemberFilters(members);
  filtered.forEach((m) => {
    if (cardList) {
      const card = document.createElement('div');
      card.className = 'member-card-item';
      if (selectedMember?.id === m.id) card.classList.add('selected');
      card.innerHTML = `
        <div class="member-card-title">
          <strong>${m.name}</strong>
          <span class="badge role">${roleLabel[m.role] || m.role}</span>
        </div>
        <div class="member-card-meta">개인 ID: ${m.identifier || '-'}</div>
        <div class="member-card-meta">로그인 ID: ${m.auth_account?.login_id || '-'}</div>
        <div class="member-card-meta">상태: ${m.active ? '활성' : '비활성'}</div>
        <div class="member-card-meta">최근 로그인: ${formatDateTimeLocal(m.auth_account?.last_login_at)}</div>
      `;
      card.addEventListener('click', () => setEditForm(m));
      cardList.appendChild(card);
      return;
    }
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${m.name}</td><td>${roleLabel[m.role] || m.role}</td><td>${m.identifier || '-'}</td><td>${m.auth_account?.login_id || '-'}</td><td>${m.active ? '활성' : '비활성'}</td><td>${formatDateTimeLocal(m.auth_account?.last_login_at)}</td>`;
    if (selectedMember?.id === m.id) tr.classList.add('selected');
    tr.addEventListener('click', () => setEditForm(m));
    tbody.appendChild(tr);
  });
  if (count) count.textContent = `${filtered.length}명 / 전체 ${members.length}명`;
}

async function loadMembers() {
  const data = await apiRequest('/users');
  let list = data || [];
  if (currentUserInfo?.role === 'OPERATOR') {
    list = list.filter((m) => m.role === 'MEMBER' || m.id === currentUserInfo.id);
  }
  members = list;
  const stillSelected = selectedMember ? members.find((m) => m.id === selectedMember.id) : null;
  if (stillSelected) {
    setEditForm(stillSelected);
  } else {
    renderMembers();
  }
}

async function deleteMember(member) {
  if (!confirm(`${member.name} 계정을 삭제하시겠습니까?`)) return;
  await apiRequest(`/users/${member.id}`, { method: 'DELETE' });
  await loadMembers();
}

async function saveMember(event) {
  event.preventDefault();
  const isEditing = Boolean(selectedMember);
  const submitBtn = document.getElementById('edit-save');
  setButtonLoading(submitBtn, true, '저장 중...');
  const identifierInput = document.getElementById('edit-identifier');
  const payload = {
    name: document.getElementById('edit-name').value,
    identifier: identifierInput?.value,
    role: document.getElementById('edit-role').value,
    active: document.getElementById('edit-active').value === 'true'
  };
  const login_id = document.getElementById('edit-login')?.value;
  const new_password = document.getElementById('edit-password')?.value;

  try {
    if (!payload.identifier) {
      const generated = `AUTO-${Math.random().toString(36).slice(2, 7).toUpperCase()}-${Date.now().toString(36).toUpperCase()}`;
      payload.identifier = generated;
      if (identifierInput) identifierInput.value = generated;
    }
    let saved;
    if (!selectedMember) {
      if (!new_password) throw new Error('신규 구성원 비밀번호를 입력하세요.');
      saved = await apiRequest('/users', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...payload, login_id, password: new_password })
      });
      alert('구성원이 생성되었습니다. 임시 비밀번호를 전달하세요.');
    } else {
      saved = await apiRequest(`/users/${selectedMember.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (editorOptions.allowCredentialEdit && (login_id || new_password)) {
        saved = await apiRequest(`/users/${selectedMember.id}/credentials`, {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            new_login_id: login_id || null,
            new_password: new_password || null
          })
        });
      }
      alert('구성원 정보가 업데이트되었습니다.');
    }
    await loadMembers();
    const latest = members.find((m) => m.id === saved.id);
    if (isEditing && latest) {
      setEditForm(latest);
    } else {
      clearEditForm();
    }
  } catch (e) {
    alert(e.message || '처리에 실패했습니다.');
  } finally {
    setButtonLoading(submitBtn, false, '저장');
    updateSaveButtonState();
  }
}

function bindMemberEvents() {
  document.getElementById('member-combined-form')?.addEventListener('submit', saveMember);
  document.getElementById('edit-cancel')?.addEventListener('click', clearEditForm);
  document.getElementById('member-refresh')?.addEventListener('click', loadMembers);
  ['edit-name', 'edit-identifier', 'edit-role', 'edit-active', 'edit-login', 'edit-password'].forEach((id) => {
    document.getElementById(id)?.addEventListener('input', updateSaveButtonState);
  });
  ['member-search', 'member-filter-role', 'member-filter-active'].forEach((id) => {
    document.getElementById(id)?.addEventListener('input', renderMembers);
  });
}

function restrictRoleOptions(role) {
  const editRole = document.getElementById('edit-role');
  const filterRole = document.getElementById('member-filter-role');
  if (role === 'OPERATOR' && editRole) {
    Array.from(editRole.options).forEach((opt) => {
      if (opt.value !== 'MEMBER') opt.remove();
    });
    editRole.value = 'MEMBER';
  }
  if (role === 'OPERATOR' && filterRole) {
    Array.from(filterRole.options).forEach((opt) => {
      if (opt.value === 'MASTER') opt.remove();
    });
  }
}

async function initMemberManagement(user) {
  currentUserInfo = user;
  editorOptions.allowCredentialEdit = user?.role === 'MASTER';
  restrictRoleOptions(user?.role);
  bindMemberEvents();
  clearEditForm();
  await loadMembers();
}

async function loadUserOptions(selectId) {
  const select = document.getElementById(selectId);
  if (!select) return;
  const users = members.length ? members : await apiRequest('/users');
  select.innerHTML = '<option value="">대상 선택</option>';
  users
    .filter((u) => u.role === 'MEMBER')
    .forEach((u) => {
      const opt = document.createElement('option');
      opt.value = u.id;
      opt.textContent = `${u.name} (${u.identifier || '개인 ID 없음'})`;
      select.appendChild(opt);
    });
}

function clearAssignSelection() {
  selectedAssignSlots.clear();
  assignGridCells.forEach((cell) => cell.classList.remove('selected'));
  updateAssignPreview();
}

function buildAssignSlotGrid() {
  const grid = document.getElementById('assign-slot-grid');
  if (!grid) return;
  grid.innerHTML = '';
  assignGridCells.clear();
  assignedSlots.clear();
  selectedAssignSlots.clear();

  const headerBlank = document.createElement('div');
  headerBlank.className = 'slot-header';
  grid.appendChild(headerBlank);
  days.forEach((day) => {
    const h = document.createElement('div');
    h.className = 'slot-header';
    h.textContent = day;
    grid.appendChild(h);
  });
  hours.forEach((hour) => {
    const label = document.createElement('div');
    label.className = 'slot-header slot-header-time';
    label.textContent = `${hour}:00`;
    grid.appendChild(label);
    days.forEach((_, weekday) => {
      const key = `${weekday}-${hour}`;
      const cell = document.createElement('div');
      cell.className = 'slot-cell';
      cell.title = `${days[weekday]} ${hour}:00-${hour + 1}:00`;
      cell.addEventListener('click', () => {
        if (selectedAssignSlots.has(key)) {
          selectedAssignSlots.delete(key);
          cell.classList.remove('selected');
          cell.classList.remove('assigned');
        } else {
          selectedAssignSlots.add(key);
          cell.classList.add('selected');
          if (assignedSlots.has(key)) cell.classList.add('assigned');
        }
        updateAssignPreview();
      });
      grid.appendChild(cell);
      assignGridCells.set(key, cell);
    });
  });
  updateAssignPreview();
  const rangeEl = appliedRangeEl();
  if (rangeEl) rangeEl.textContent = '';
}

function updateAssignPreview() {
  const preview = document.getElementById('assign-slot-preview');
  if (!preview) return;
  if (!selectedAssignSlots.size) {
    preview.textContent = '요일·시간 칸을 터치하여 배정할 슬롯을 선택하세요.';
    return;
  }
  const ranges = slotsToRanges(selectedAssignSlots);
  const selectedTexts = ranges.map((r) => `${days[r.weekday]} ${String(r.start_hour).padStart(2, '0')}:00~${String(r.end_hour).padStart(2, '0')}:00`);
  preview.textContent = `${selectedTexts.length}개 구간 선택: ${selectedTexts.join(', ')}`;
}

function markAssignedSlotRange(weekday, startHour, endHour) {
  for (let h = Math.max(9, startHour); h < Math.min(18, endHour); h++) {
    const key = `${weekday}-${h}`;
    assignedSlots.add(key);
    const cell = assignGridCells.get(key);
    if (cell) cell.classList.add('assigned', 'selected');
    selectedAssignSlots.add(key);
  }
}

async function refreshAssignedSlotsForUser() {
  assignedSlots.clear();
  assignGridCells.forEach((cell) => cell.classList.remove('assigned'));
  clearAssignSelection();
  const user_id = document.getElementById('assign-user')?.value;
  if (user_id !== lastAssignUserId) {
    manualDateOverride.from = false;
    manualDateOverride.to = false;
    lastAssignUserId = user_id;
  }
  if (!user_id) return true;
  const fromInput = document.getElementById('assign-from')?.value || formatDateOnlyLocal(new Date());
  const params = new URLSearchParams({ start: weekStart(fromInput), user_id });
  const rangeEl = appliedRangeEl();
  if (rangeEl) rangeEl.textContent = '배정 일자를 불러오는 중...';
  try {
    let events = await apiRequest(`/schedule/weekly_base?${params.toString()}`);
    let minFrom = null;
    let maxTo = null;
    const fromInputEl = document.getElementById('assign-from');
    const toInputEl = document.getElementById('assign-to');
    if (!events.length) {
      const snapshot = await apiRequest('/schedule/global');
      const assignments = (snapshot?.assignments || []).filter((assignment) => assignment.user?.id === user_id);
      events = assignments.map((assignment) => ({
        date: assignment.valid_from,
        start_time: assignment.shift?.start_time,
        end_time: assignment.shift?.end_time,
        valid_from: assignment.valid_from,
        valid_to: assignment.valid_to,
        shift: assignment.shift,
        source: 'BASE'
      }));
    }
    events.forEach((ev) => {
      if (ev.source === 'BASE') {
        const fromDate = ev.valid_from ? new Date(ev.valid_from) : null;
        const toDate = ev.valid_to ? new Date(ev.valid_to) : null;
        if (fromDate && (!minFrom || fromDate < minFrom)) minFrom = fromDate;
        if (toDate && (!maxTo || toDate > maxTo)) maxTo = toDate;
      }
      if (ev.shift) {
        const startHour = parseInt(ev.shift.start_time.split(':')[0], 10);
        const endHour = parseInt(ev.shift.end_time.split(':')[0], 10);
        markAssignedSlotRange(ev.shift.weekday, startHour, endHour);
        return;
      }
      const dateObj = parseDateValue(ev.date);
      const weekday = (dateObj.getDay() + 6) % 7;
      const startHour = parseInt(ev.start_time.split(':')[0], 10);
      const endHour = parseInt(ev.end_time.split(':')[0], 10);
      markAssignedSlotRange(weekday, startHour, endHour);
    });
    if (rangeEl) {
      if (minFrom || maxTo) {
        const fromText = minFrom ? formatDateOnlyLocal(minFrom) : '시작일 미상';
        const toText = maxTo ? formatDateOnlyLocal(maxTo) : '계속 적용';
        rangeEl.textContent = `현재 불러온 배정 적용 기간: ${fromText} ${maxTo ? `~ ${toText}` : '(계속 적용)'}`;
      } else {
        rangeEl.textContent = '적용 기간 정보가 없습니다.';
      }
    }
    if (fromInputEl && minFrom && !manualDateOverride.from) {
      fromInputEl.value = formatDateOnlyLocal(minFrom);
    }
    if (toInputEl && !manualDateOverride.to) {
      toInputEl.value = maxTo ? formatDateOnlyLocal(maxTo) : '';
    }
    updateAssignPreview();
  } catch (e) {
    console.error('배정 슬롯 불러오기 실패', e);
    if (rangeEl) rangeEl.textContent = '배정 적용 기간을 불러오지 못했습니다.';
    return false;
  }
  return true;
}

async function assignShift(event) {
  event.preventDefault();
  if (!selectedAssignSlots.size) {
    alert('요일·시간 슬롯을 최소 1개 이상 선택하세요.');
    return;
  }
  const user_id = document.getElementById('assign-user')?.value;
  const valid_from = document.getElementById('assign-from')?.value;
  const valid_to_raw = document.getElementById('assign-to')?.value || '';
  const valid_to = valid_to_raw || null;
  if (!valid_from) {
    alert('적용 시작일을 입력하세요.');
    return;
  }
  if (valid_to && valid_to < valid_from) {
    alert('종료일은 시작일 이후여야 합니다.');
    return;
  }
  if (!user_id) {
    alert('근무를 배정할 구성원을 선택하세요.');
    return;
  }
  const submitBtn = event.target.querySelector('button[type="submit"]');
  setButtonLoading(submitBtn, true, '배정 중...');
  try {
    const slots = slotsToRanges(selectedAssignSlots).map((slot) => ({
      weekday: slot.weekday,
      start_hour: slot.start_hour,
      end_hour: slot.end_hour
    }));
    const payload = { user_id, valid_from, slots };
    if (valid_to) payload.valid_to = valid_to;
    await apiRequest('/schedule/slots/bulk_assign', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    alert('선택한 근무 시간이 저장되었습니다.');
    const refreshed = await refreshAssignedSlotsForUser();
    if (refreshed === false) {
      window.location.reload();
    }
  } catch (e) {
    alert(e.message || '근무 배정 중 오류가 발생했습니다.');
  } finally {
    await refreshAssignedSlotsForUser();
    setButtonLoading(submitBtn, false, '선택 슬롯 배정');
  }
}

function slotsToRanges(slotKeys) {
  const grouped = new Map();
  slotKeys.forEach((key) => {
    const [weekday, hour] = key.split('-').map(Number);
    if (!grouped.has(weekday)) grouped.set(weekday, []);
    grouped.get(weekday).push(hour);
  });
  const ranges = [];
  grouped.forEach((hoursList, weekday) => {
    const sorted = hoursList.sort((a, b) => a - b);
    let start = sorted[0];
    let prev = sorted[0];
    for (let i = 1; i < sorted.length; i++) {
      if (sorted[i] === prev + 1) {
        prev = sorted[i];
        continue;
      }
      ranges.push({ weekday, start_hour: start, end_hour: prev + 1 });
      start = prev = sorted[i];
    }
    ranges.push({ weekday, start_hour: start, end_hour: prev + 1 });
  });
  return ranges;
}

function bindAssignDateInputs() {
  document.getElementById('assign-from')?.addEventListener('input', () => { manualDateOverride.from = true; });
  document.getElementById('assign-to')?.addEventListener('input', () => { manualDateOverride.to = true; });
}

export { initMemberManagement, assignShift, loadUserOptions, buildAssignSlotGrid, refreshAssignedSlotsForUser, bindAssignDateInputs };

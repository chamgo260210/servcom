// File: /ui/js/profile.js
import { apiRequest, redirectToLogin } from './api.js';
import { setupPasswordToggle, markPasswordUpdated } from './auth.js';

const roleLabel = {
  MASTER: '마스터',
  OPERATOR: '운영자',
  MEMBER: '구성원'
};

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? '-';
}

function renderProfile(user) {
  setText('profile-name', user?.name || '-');
  setText('profile-role', user ? `${roleLabel[user.role] || user.role} (${user.role})` : '-');
  setText('profile-identifier', user?.identifier || '-');
  setText('profile-login', user?.auth_account?.login_id || '-');
  setText('profile-active', user?.active ? '활성' : '비활성');
  setText('profile-last-login', user?.auth_account?.last_login_at ? new Date(user.auth_account.last_login_at).toLocaleString() : '기록 없음');
}

function showAccountWarning(message, tips = []) {
  const box = document.getElementById('account-warning');
  if (!box) return;
  const list = tips.map((t) => `• ${t}`).join('<br />');
  box.innerHTML = `${message}${list ? '<br />' + list : ''}`;
}

async function loadVisibleUsers() {
  const tbody = document.getElementById('visible-users-body');
  const cardList = document.getElementById('visible-users-list');
  if (!tbody && !cardList) return;
  const users = await apiRequest('/users');
  if (tbody) tbody.innerHTML = '';
  if (cardList) cardList.innerHTML = '';
  users.forEach((u) => {
    if (cardList) {
      const card = document.createElement('div');
      card.className = 'member-card-item';
      card.innerHTML = `
        <div class="member-card-title">
          <strong>${u.name}</strong>
          <span class="badge role">${roleLabel[u.role] || u.role}</span>
        </div>
        <div class="member-card-meta">개인 ID: ${u.identifier || '-'}</div>
        <div class="member-card-meta">로그인 ID: ${u.auth_account?.login_id || '-'}</div>
        <div class="member-card-meta">상태: ${u.active ? '활성' : '비활성'}</div>
      `;
      cardList.appendChild(card);
      return;
    }
    const tr = document.createElement('tr');
    tr.innerHTML = `<td>${u.name}</td><td>${roleLabel[u.role] || u.role}</td><td>${u.identifier || ''}</td><td>${u.auth_account?.login_id || ''}</td><td>${u.active ? '활성' : '비활성'}</td>`;
    tbody.appendChild(tr);
  });
}

function bindAccountForm() {
  const form = document.getElementById('account-form');
  if (!form) return;
  setupPasswordToggle('current-password', 'toggle-current');
  setupPasswordToggle('new-password', 'toggle-new');
  setupPasswordToggle('confirm-password', 'toggle-confirm');
  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      current_password: document.getElementById('current-password')?.value,
      new_login_id: document.getElementById('new-login')?.value || null,
      new_password: document.getElementById('new-password')?.value || null
    };
    const confirm = document.getElementById('confirm-password')?.value || '';
    if (!payload.new_login_id && !payload.new_password) {
      showAccountWarning('변경할 로그인 ID나 비밀번호를 입력하세요.', ['필요한 항목만 작성 후 다시 시도']);
      return;
    }
    if (payload.new_password && payload.new_password.length < 8) {
      showAccountWarning('새 비밀번호는 8자 이상이어야 합니다.', ['영문/숫자/기호를 섞어 보안을 강화하세요.']);
      return;
    }
    if (payload.new_password) {
      const complexityOk = /^(?=.*[0-9])(?=.*[!@#$%^&*()_\-+=\[{\]}|;:'\",.<>/?`~]).{8,}$/.test(payload.new_password);
      if (!complexityOk) {
        showAccountWarning('숫자와 기호를 각각 1개 이상 포함해야 합니다.', ['예: Abcd1234! 처럼 조합해 주세요.']);
        return;
      }
    }
    if (payload.new_password && payload.new_password !== confirm) {
      showAccountWarning('비밀번호 확인이 일치하지 않습니다.', ['새 비밀번호와 확인란을 동일하게 입력하세요.']);
      return;
    }
    try {
      const updated = await apiRequest('/auth/account', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      renderProfile(updated);
      form.reset();
      showAccountWarning('계정 정보가 업데이트되었습니다.', ['필요 시 다시 로그인하세요.']);
      markPasswordUpdated();
    } catch (err) {
      const message = err?.message || '업데이트에 실패했습니다.';
      const tips = [];
      if (message.includes('8 characters') || message.includes('length')) {
        tips.push('비밀번호는 8자 이상이어야 합니다.');
      }
      if (message.toLowerCase().includes('login id')) {
        tips.push('이미 사용 중인 로그인 ID입니다. 다른 값을 입력하세요.');
      }
      tips.push('현재 비밀번호를 정확히 입력했는지 확인하세요.');
      showAccountWarning(`오류: ${message}`, tips);
    }
  });
}

function bindResetButtons(role) {
  const buttons = document.querySelectorAll('[data-reset-scope]');
  const allowedByRole = {
    MASTER: ['members', 'operators_members', 'all'],
    OPERATOR: ['members'],
    MEMBER: []
  };
  const resetCard = document.getElementById('reset-card');
  buttons.forEach((btn) => {
    const scope = btn.dataset.resetScope;
    const isAllowed = allowedByRole[role]?.includes(scope);
    btn.style.display = isAllowed ? '' : 'none';
    if (!isAllowed) return;
    btn.addEventListener('click', async () => {
      const confirmMsg = `정말로 ${btn.textContent.trim()} 작업을 진행하시겠습니까?`;
      if (!confirm(confirmMsg)) return;
      try {
        const result = await apiRequest('/reset', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ scope })
        });
        const label = document.getElementById('reset-result');
        if (label) label.textContent = `${new Date().toLocaleTimeString()} · ${result.detail}`;
        alert('초기화가 완료되어 다시 로그인합니다.');
        redirectToLogin();
      } catch (err) {
        alert(err.message);
      }
    });
  });
  if (resetCard) {
    const anyVisible = Array.from(buttons).some((b) => b.style.display !== 'none');
    resetCard.style.display = anyVisible ? '' : 'none';
  }
}

function applyProfileVisibility(role) {
  const visibleUsersCard = document.getElementById('visible-users-card');
  const resetCard = document.getElementById('reset-card');
  const assignmentsCard = document.getElementById('assignments-card');

  if (role === 'MEMBER') {
    if (visibleUsersCard) visibleUsersCard.style.display = 'none';
    if (resetCard) resetCard.style.display = 'none';
    if (assignmentsCard) assignmentsCard.style.display = '';
  } else if (role === 'OPERATOR') {
    if (visibleUsersCard) visibleUsersCard.style.display = '';
    if (resetCard) resetCard.style.display = '';
    if (assignmentsCard) assignmentsCard.style.display = 'none';
  } else {
    if (visibleUsersCard) visibleUsersCard.style.display = '';
    if (resetCard) resetCard.style.display = '';
    if (assignmentsCard) assignmentsCard.style.display = '';
  }
}

async function attachProfilePage(user) {
  if (!user) return;
  renderProfile(user);
  bindAccountForm();
  bindResetButtons(user.role);
  applyProfileVisibility(user.role);
  if (user.role !== 'MEMBER') {
    await loadVisibleUsers();
  }
}

export { attachProfilePage };

// File: /ui/js/auth.js
import { apiRequest, API_BASE_URL, clearToken, redirectToLogin, setToken, parseTokenExp } from './api.js';

let countdownInterval;
const PASSWORD_RULE = /^(?=.*[0-9])(?=.*[!@#$%^&*()_\-+=\[{\]}|;:'",.<>/?`~]).{8,}$/;
const PASSWORD_FLAG_KEY = 'needs_password_update';
const PASSWORD_SNOOZE_KEY = 'password_update_snooze_until';

function wait(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function buildDashboardUrl() {
  const path = window.location.pathname;
  if (path.includes('/mobile/')) {
    const base = path.split('/mobile/')[0];
    return `${window.location.origin}${base}/mobile/dashboard.html`;
  }
  const base = path.includes('/html/')
    ? path.split('/html/')[0]
    : path.replace(/\/[^/]*$/, '');
  return `${window.location.origin}${base}/html/dashboard.html`;
}

async function warmupBackend({ retries = 2, delayMs = 600, timeoutMs = 6000 } = {}) {
  for (let attempt = 0; attempt <= retries; attempt += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const resp = await fetch(`${API_BASE_URL}/health`, { cache: 'no-store', signal: controller.signal });
      if (!resp.ok) throw new Error('health_failed');
      return;
    } catch (err) {
      if (attempt >= retries) throw err;
      await wait(delayMs * (attempt + 1));
    } finally {
      clearTimeout(timer);
    }
  }
}

async function login(event) {
  event.preventDefault();
  const login_id = document.getElementById('login_id').value;
  const password = document.getElementById('password').value;
  const button = event.target.querySelector('button[type="submit"]');
  const loadingBar = document.getElementById('login-loading');
  const statusText = document.getElementById('login-progress');
  if (button) button.disabled = true;
  if (loadingBar) loadingBar.classList.add('show');
  if (statusText) statusText.textContent = '서버 깨우는 중...';
  const form = new FormData();
  form.append('username', login_id);
  form.append('password', password);

  try {
    await warmupBackend();
    if (statusText) statusText.textContent = '로그인 처리 중...';
    const res = await fetch(`${API_BASE_URL}/auth/login`, {
      method: 'POST',
      body: form
    });
    if (!res.ok) throw new Error('로그인에 실패했습니다');
    const data = await res.json();
    setToken(data.access_token);
    if (isWeakPassword(password)) {
      localStorage.setItem(PASSWORD_FLAG_KEY, '1');
      localStorage.removeItem(PASSWORD_SNOOZE_KEY);
    } else {
      localStorage.removeItem(PASSWORD_FLAG_KEY);
      localStorage.removeItem(PASSWORD_SNOOZE_KEY);
    }
    window.location.href = buildDashboardUrl();
  } catch (err) {
    alert(err.message);
    if (statusText) statusText.textContent = '로그인에 실패했습니다. 다시 시도해주세요.';
  }
  if (loadingBar) loadingBar.classList.remove('show');
  if (button) button.disabled = false;
}

async function loadUser() {
  try {
    const user = await apiRequest('/auth/me');
    const nameNode = document.getElementById('user-name');
    if (nameNode) nameNode.innerText = user.name;
    const roleNode = document.getElementById('user-role');
    if (roleNode) roleNode.innerText = user.role;
    return user;
  } catch (e) {
    console.error(e);
  }
}

function logout() {
  sessionStorage.removeItem('notice_popup_seen');
  redirectToLogin();
}

function isWeakPassword(pw) {
  return !PASSWORD_RULE.test(pw);
}

function markPasswordUpdated() {
  localStorage.removeItem(PASSWORD_FLAG_KEY);
  localStorage.removeItem(PASSWORD_SNOOZE_KEY);
}

function needsPasswordUpdate() {
  return localStorage.getItem(PASSWORD_FLAG_KEY) === '1';
}

function snoozePasswordUpdate(hours = 24) {
  const until = Date.now() + hours * 60 * 60 * 1000;
  localStorage.setItem(PASSWORD_SNOOZE_KEY, String(until));
}

function shouldShowPasswordUpdatePrompt() {
  if (!needsPasswordUpdate()) return false;
  const snoozeUntil = parseInt(localStorage.getItem(PASSWORD_SNOOZE_KEY) || '0', 10);
  return !snoozeUntil || Date.now() > snoozeUntil;
}

async function refreshSession() {
  const data = await apiRequest('/auth/refresh', { method: 'POST' });
  setToken(data.access_token);
  return parseTokenExp(data.access_token);
}

function startSessionCountdown(el, extendBtn) {
  if (!el) return;
  const exp = parseInt(localStorage.getItem('token_exp') || '0', 10) || parseTokenExp(localStorage.getItem('token'));
  if (countdownInterval) clearInterval(countdownInterval);
  if (!exp) {
    el.textContent = '';
    return;
  }
  const tick = () => {
    const remaining = exp - Date.now();
    if (remaining <= 0) {
      el.textContent = '세션 만료됨';
      logout(true);
      return;
    }
    const mins = Math.floor(remaining / 60000);
    const secs = Math.floor((remaining % 60000) / 1000);
    el.textContent = `자동 로그아웃까지 ${mins}분 ${secs.toString().padStart(2, '0')}초`;

    if (extendBtn) {
      const shouldShow = remaining <= 5 * 60 * 1000;
      extendBtn.style.display = shouldShow ? 'inline-flex' : 'none';
      extendBtn.disabled = false;
    }
  };
  tick();
  countdownInterval = setInterval(tick, 1000);
}

function setupPasswordToggle(inputId, toggleId) {
  const input = document.getElementById(inputId);
  const toggle = document.getElementById(toggleId);
  if (!input || !toggle) return;
  const showIcon = '●';
  const hideIcon = '○';
  toggle.textContent = hideIcon;
  toggle.addEventListener('click', () => {
    const isHidden = input.type === 'password';
    input.type = isHidden ? 'text' : 'password';
    toggle.textContent = isHidden ? showIcon : hideIcon;
  });
}

export {
  login,
  loadUser,
  logout,
  startSessionCountdown,
  setupPasswordToggle,
  refreshSession,
  isWeakPassword,
  markPasswordUpdated,
  needsPasswordUpdate,
  snoozePasswordUpdate,
  shouldShowPasswordUpdatePrompt
};

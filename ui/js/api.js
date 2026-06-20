// File: /ui/js/api.js
const API_BASE_URL = (() => {
  const configured = window.localStorage.getItem('api_base_url_override');
  if (configured) return configured.replace(/\/$/, '');
  return '/api';
})();
const SESSION_EXPIRED_MESSAGE = '다른 위치에서 로그인되어 현재 세션이 종료되었습니다. 계속 사용하려면 다시 로그인해 주세요.';
const SESSION_CHECK_THROTTLE_MS = 60_000;

function parseTokenExp(token) {
  if (!token) return null;
  const parts = token.split('.');
  if (parts.length < 2) return null;
  try {
    const base = parts[1].replace(/-/g, '+').replace(/_/g, '/');
    const payload = JSON.parse(atob(base));
    if (payload.exp) return payload.exp * 1000;
  } catch (e) {
    console.error('Failed to parse token exp', e);
  }
  return null;
}

function setToken(token) {
  localStorage.setItem('token', token);
  const exp = parseTokenExp(token);
  if (exp) localStorage.setItem('token_exp', String(exp));
}

function buildLoginUrl() {
  const path = window.location.pathname;
  const base = path.includes('/html/')
    ? path.split('/html/')[0]
    : path.replace(/\/[^/]*$/, '/');
  const normalized = base.endsWith('/') ? base : `${base}/`;
  return `${window.location.origin}${normalized}index.html`;
}

function redirectToLogin(reason = '') {
  clearToken();
  if (reason) sessionStorage.setItem('auth_logout_reason', reason);
  window.location.replace(buildLoginUrl());
}

function getToken() {
  return localStorage.getItem('token');
}

function clearToken() {
  localStorage.removeItem('token');
  localStorage.removeItem('token_exp');
}

let sessionExpiredModalShown = false;
let sessionBlocked = false;
let lastSessionCheckAt = 0;
let lastSuccessfulApiAt = 0;

function isSessionBlocked() {
  return sessionBlocked;
}

function markSuccessfulApi() {
  lastSuccessfulApiAt = Date.now();
}

function showSessionExpiredModal(message = SESSION_EXPIRED_MESSAGE) {
  sessionBlocked = true;
  if (sessionExpiredModalShown) return;
  sessionExpiredModalShown = true;

  const existing = document.getElementById('session-expired-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'session-expired-modal';
  modal.className = 'modal-backdrop';
  modal.innerHTML = `
    <div class="modal">
      <div class="modal-header">
        <h3>세션이 종료되었습니다</h3>
      </div>
      <div class="modal-body">
        <p>${message}</p>
      </div>
      <div class="modal-footer">
        <button class="btn" id="session-expired-confirm" type="button">확인</button>
      </div>
    </div>
  `;

  document.body.appendChild(modal);

  const goLogin = () => {
    sessionStorage.removeItem('auth_logout_reason');
    window.location.replace(buildLoginUrl());
  };

  document.getElementById('session-expired-confirm')?.addEventListener('click', goLogin);

  // 사용자가 Enter를 눌러도 로그인 화면으로 이동
  modal.tabIndex = -1;
  modal.focus();
  modal.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') goLogin();
  });
}

async function checkSessionAlive({ force = false } = {}) {
  if (sessionBlocked) {
    showSessionExpiredModal();
    return false;
  }

  const token = getToken();
  if (!token) {
    showSessionExpiredModal();
    return false;
  }

  const now = Date.now();
  const lastKnownAliveAt = Math.max(lastSessionCheckAt, lastSuccessfulApiAt);
  if (!force && lastKnownAliveAt && now - lastKnownAliveAt < SESSION_CHECK_THROTTLE_MS) {
    return true;
  }

  lastSessionCheckAt = now;
  const requestMe = async (accessToken) => fetch(`${API_BASE_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
    cache: 'no-store'
  });

  try {
    let resp = await requestMe(token);
    if (resp.status === 401) {
      const refreshed = await refreshToken();
      if (refreshed) resp = await requestMe(refreshed);
    }
    if (resp.ok) {
      markSuccessfulApi();
      return true;
    }
  } catch (e) {
    console.warn('Session check failed', e);
  }

  showSessionExpiredModal();
  return false;
}

async function refreshToken() {
  const token = getToken();
  if (!token) return null;
  const resp = await fetch(`${API_BASE_URL}/auth/refresh`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!resp.ok) throw new Error('refresh_failed');
  const data = await resp.json();
  if (data?.access_token) setToken(data.access_token);
  return data?.access_token;
}

async function apiRequest(path, options = {}) {
  if (sessionBlocked) {
    showSessionExpiredModal();
    return;
  }

  const { responseType, __noRetry, ...requestOptions } = options;
  const headers = options.headers ? { ...options.headers } : {};
  let token = getToken();
  const exp = parseInt(localStorage.getItem('token_exp') || '0', 10);
  if (token && exp && exp - Date.now() < 5_000) {
    try {
      token = await refreshToken();
    } catch (e) {
      showSessionExpiredModal();
      return;
    }
    if (token) headers.Authorization = `Bearer ${token}`;
  }
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  let resp = await fetch(`${API_BASE_URL}${path}`, { ...requestOptions, headers });
  if (resp.status === 401 && !__noRetry && token) {
    try {
      const newToken = await refreshToken();
      if (newToken) {
        const retryHeaders = { ...headers, Authorization: `Bearer ${newToken}` };
        resp = await fetch(`${API_BASE_URL}${path}`, { ...requestOptions, headers: retryHeaders });
      }
    } catch (e) {
      // 최종 401 처리에서 세션 만료 모달을 표시한다.
    }
  }
  if (resp.status === 401) {
    showSessionExpiredModal();
    return;
  }
  if (!resp.ok) {
    let message = '요청에 실패했습니다';
    try {
      const data = await resp.json();
      if (data?.detail) message = typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail);
      else if (data?.message) message = data.message;
      else message = JSON.stringify(data);
    } catch (e) {
      const text = await resp.text();
      if (text) message = text;
    }
    throw new Error(message);
  }
  markSuccessfulApi();
  if (resp.status === 204) return null;
  if (responseType === 'blob') return resp;
  return await resp.json();
}

export {
  API_BASE_URL,
  apiRequest,
  getToken,
  clearToken,
  redirectToLogin,
  setToken,
  parseTokenExp,
  showSessionExpiredModal,
  isSessionBlocked,
  checkSessionAlive
};

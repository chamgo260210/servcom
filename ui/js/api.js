// File: /ui/js/api.js
const API_BASE_URL = "https://work-time-back.onrender.com";

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

function redirectToLogin() {
  clearToken();
  window.location.replace(buildLoginUrl());
}

function getToken() {
  return localStorage.getItem('token');
}

function clearToken() {
  localStorage.removeItem('token');
  localStorage.removeItem('token_exp');
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
  const headers = options.headers ? { ...options.headers } : {};
  let token = getToken();
  const exp = parseInt(localStorage.getItem('token_exp') || '0', 10);
  if (token && exp && exp - Date.now() < 5_000) {
    try {
      token = await refreshToken();
    } catch (e) {
      clearToken();
      redirectToLogin();
      return;
    }
    if (token) headers['Authorization'] = `Bearer ${token}`;
  }
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  let resp = await fetch(`${API_BASE_URL}${path}`, { ...options, headers });
  if (resp.status === 401 && !options.__noRetry && token) {
    try {
      const newToken = await refreshToken();
      if (newToken) {
        const retryHeaders = { ...headers, Authorization: `Bearer ${newToken}` };
        resp = await fetch(`${API_BASE_URL}${path}`, { ...options, headers: retryHeaders, __noRetry: true });
      }
    } catch (e) {
      // fall through to redirect
    }
  }
  if (resp.status === 401) {
    redirectToLogin();
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
  if (resp.status === 204) return null;
  return await resp.json();
}

export { API_BASE_URL, apiRequest, getToken, clearToken, redirectToLogin, setToken, parseTokenExp };

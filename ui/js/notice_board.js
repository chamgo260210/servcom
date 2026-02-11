import { apiRequest } from './api.js';
import { getNoticeTypeLabel, formatNoticeDate, markNoticeRead } from './notices.js';
import { initAppLayout } from './layout.js';

const listEl = document.getElementById('notice-list');
const emptyEl = document.getElementById('notice-empty');

await initAppLayout('notices');

async function loadNotices() {
  if (listEl) listEl.innerHTML = '<div class="loader">공지사항을 불러오는 중...</div>';
  try {
    const notices = await apiRequest('/notices?channel=BOARD');
    if (!notices.length) {
      if (listEl) listEl.innerHTML = '';
      if (emptyEl) emptyEl.style.display = 'block';
      return;
    }
    if (emptyEl) emptyEl.style.display = 'none';
    renderNotices(notices);
  } catch (e) {
    if (listEl) listEl.innerHTML = `<div class="error">공지사항을 불러오지 못했습니다: ${e.message || e}</div>`;
  }
}

function renderNotices(notices) {
  if (!listEl) return;
  listEl.innerHTML = '';
  notices.forEach((notice) => {
    const card = document.createElement('div');
    card.className = 'notice-card';
    const header = document.createElement('div');
    header.className = 'notice-card-header';
    const title = document.createElement('div');
    title.className = 'notice-card-title';
    title.innerHTML = `<span class="notice-tag">${getNoticeTypeLabel(notice.type)}</span><strong>${notice.title}</strong>`;
    const date = document.createElement('div');
    date.className = 'muted small';
    date.textContent = notice.start_at ? formatNoticeDate(notice.start_at) : formatNoticeDate(notice.created_at);
    header.appendChild(title);
    header.appendChild(date);
    const body = document.createElement('div');
    body.className = 'notice-card-body';
    body.textContent = notice.body;
    card.appendChild(header);
    card.appendChild(body);
    listEl.appendChild(card);

    markNoticeRead(notice.id, 'BOARD').catch(() => {});
  });
}

loadNotices();

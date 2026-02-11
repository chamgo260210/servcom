import { apiRequest } from './api.js';

const typeLabels = {
  DB_MAINTENANCE: 'DB 점검',
  SYSTEM_MAINTENANCE: '시스템 점검',
  WORK_SPECIAL: '특별 근무',
  GENERAL: '일반 공지'
};

function formatDate(value) {
  if (!value) return '';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('ko-KR', { hour12: false });
}

function buildNoticeTag(notice) {
  const span = document.createElement('span');
  span.className = 'notice-tag';
  span.textContent = typeLabels[notice.type] || notice.type;
  return span;
}

async function dismissNotice(id, channel) {
  await apiRequest(`/notices/${id}/dismiss`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel })
  });
}

export async function markNoticeRead(id, channel) {
  await apiRequest(`/notices/${id}/read`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ channel })
  });
}

export async function initNoticeOverlays() {
  const bannerContainerId = 'notice-banner-container';
  let bannerContainer = document.getElementById(bannerContainerId);
  if (!bannerContainer) {
    bannerContainer = document.createElement('div');
    bannerContainer.id = bannerContainerId;
    bannerContainer.className = 'notice-banner-container';
    const header = document.querySelector('.header');
    if (header?.parentNode) {
      header.parentNode.insertBefore(bannerContainer, header.nextSibling);
    } else {
      document.body.prepend(bannerContainer);
    }
  }

  let bannerNotices = [];
  try {
    bannerNotices = await apiRequest('/notices?channel=BANNER');
  } catch (e) {
    console.warn('배너 공지 로드 실패', e);
  }

  bannerContainer.innerHTML = '';
  if (bannerNotices.length) {
    let bannerIndex = 0;
    const renderBanner = () => {
      const notice = bannerNotices[bannerIndex];
      if (!notice) return;
      bannerContainer.innerHTML = '';
      const banner = document.createElement('div');
      banner.className = 'notice-banner';

      const content = document.createElement('div');
      content.className = 'notice-banner-content';

      const tag = buildNoticeTag(notice);
      tag.classList.add('notice-banner-tag');
      content.appendChild(tag);

      const text = document.createElement('div');
      text.className = 'notice-banner-text';
      text.innerHTML = `
        <div class="notice-banner-marquee">
          <strong>${notice.title}</strong>
          <span class="notice-banner-body">${notice.body}</span>
        </div>
      `;
      content.appendChild(text);

      const action = document.createElement('div');
      action.className = 'notice-banner-actions';
      const link = document.createElement('a');
      link.className = 'btn tiny';
      link.href = 'notice_board.html';
      link.textContent = '공지사항 보기';
      action.appendChild(link);
      const counter = document.createElement('span');
      counter.className = 'notice-banner-count';
      counter.textContent = `${bannerIndex + 1} / ${bannerNotices.length}`;
      action.appendChild(counter);

      banner.appendChild(content);
      banner.appendChild(action);
      bannerContainer.appendChild(banner);
      bannerIndex = (bannerIndex + 1) % bannerNotices.length;
    };
    renderBanner();
    setInterval(renderBanner, 8000);
  }

  if (sessionStorage.getItem('notice_popup_seen') === 'true') {
    return;
  }

  let popupNotices = [];
  try {
    popupNotices = await apiRequest('/notices?channel=POPUP');
  } catch (e) {
    console.warn('팝업 공지 로드 실패', e);
  }
  if (!popupNotices.length) return;

  let popupIndex = 0;
  const renderPopup = () => {
    const notice = popupNotices[popupIndex];
    if (!notice) return;
    const existing = document.getElementById('notice-popup-modal');
    if (existing) existing.remove();
    const modal = document.createElement('div');
    modal.id = 'notice-popup-modal';
    modal.className = 'modal-backdrop notice-popup-backdrop';
    modal.innerHTML = `
      <div class="modal notice-popup">
        <div class="modal-header notice-popup-header">
          <div class="notice-popup-title">
            <span class="notice-tag">${typeLabels[notice.type] || notice.type}</span>
            <h3>${notice.title}</h3>
          </div>
          <div class="muted small">${notice.start_at ? formatDate(notice.start_at) : '공지사항'}</div>
        </div>
        <div class="modal-body notice-popup-body">
          <p>${notice.body}</p>
          <label class="inline notice-snooze">
            <input type="checkbox" id="notice-popup-snooze" /> 오늘 하루 보지 않기
          </label>
        </div>
        <div class="modal-footer notice-popup-footer">
          <a class="btn secondary" href="notice_board.html">공지사항 보기</a>
          <button class="btn" id="notice-popup-confirm">확인</button>
        </div>
      </div>
    `;
    document.body.appendChild(modal);
    sessionStorage.setItem('notice_popup_seen', 'true');
    document.getElementById('notice-popup-confirm')?.addEventListener('click', async () => {
      const snooze = document.getElementById('notice-popup-snooze');
      const snoozeChecked = snooze && snooze.checked;
      try {
        if (snoozeChecked) {
          await dismissNotice(notice.id, 'POPUP');
        } else {
          await markNoticeRead(notice.id, 'POPUP');
        }
      } catch (e) {
        console.warn('팝업 기록 실패', e);
      }
      modal.remove();
      popupIndex += 1;
      if (popupIndex < popupNotices.length) {
        renderPopup();
      }
    });
  };

  renderPopup();
}

export function getNoticeTypeLabel(type) {
  return typeLabels[type] || type;
}

export function formatNoticeDate(value) {
  return formatDate(value);
}

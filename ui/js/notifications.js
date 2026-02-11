import { apiRequest } from './api.js';

const bellSvg = `
<svg aria-hidden="true" focusable="false" width="22" height="22" viewBox="0 0 24 24" fill="none">
  <path d="M12 3a6 6 0 0 0-6 6v4.4l-.92 2.3A1 1 0 0 0 6.02 17h11.96a1 1 0 0 0 .94-1.3l-.92-2.3V9a6 6 0 0 0-6-6Z" stroke="currentColor" stroke-width="1.5" fill="currentColor" fill-opacity="0.06"/>
  <path d="M9.5 18a2.5 2.5 0 0 0 5 0" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/>
</svg>
`;

const statusText = {
  PENDING: '승인 대기',
  APPROVED: '승인됨',
  REJECTED: '거절됨',
  CANCELLED: '취소됨',
};

function buildItem(text, status, meta) {
  const li = document.createElement('li');
  li.className = `notif-item ${status ? status.toLowerCase() : ''}`;
  li.innerHTML = `<div>${text}</div>${meta ? `<div class="muted small">${meta}</div>` : ''}`;
  return li;
}

const cache = {
  users: null,
  shifts: null
};

async function ensureMeta() {
  if (!cache.users) cache.users = apiRequest('/users');
  if (!cache.shifts) cache.shifts = apiRequest('/schedule/shifts');
  const [users, shifts] = await Promise.all([cache.users, cache.shifts]);
  return { users, shifts };
}

function timeWindow(req, shift) {
  const start = req.target_start_time || shift?.start_time;
  const end = req.target_end_time || shift?.end_time;
  if (!start || !end) return shift ? `${shift.start_time?.slice(0, 5)}~${shift.end_time?.slice(0, 5)}` : '';
  return `${start.slice(0, 5)}~${end.slice(0, 5)}`;
}

function buildRequestEvents(requests, userMap, shiftMap, viewerRole) {
  const events = [];
  requests.forEach((req) => {
    const applicant = userMap[req.user_id]?.name || '알 수 없는 신청자';
    const shift = shiftMap[req.target_shift_id];
    const window = timeWindow(req, shift);
    const kind = req.type === 'ABSENCE' ? '결근' : '추가 근무';
    const base = `${req.target_date} ${window ? `${window} ` : ''}${kind}`;
    const reasonMeta = req.reason ? `사유: ${req.reason}` : null;

    if (viewerRole === 'MEMBER') {
      events.push({
        id: `${req.id}-submit`,
        text: `변경 요청이 접수되었습니다: ${base}`,
        meta: reasonMeta,
        status: 'PENDING',
        created_at: req.created_at
      });
      if (req.status === 'APPROVED') {
        events.push({
          id: `${req.id}-approved`,
          text: `접수한 신청이 승인되었습니다: ${base}`,
          meta: reasonMeta,
          status: 'APPROVED',
          created_at: req.decided_at || req.created_at
        });
      } else if (req.status === 'REJECTED') {
        events.push({
          id: `${req.id}-rejected`,
          text: `접수한 신청이 거부되었습니다: ${base}`,
          meta: reasonMeta || '거부됨',
          status: 'REJECTED',
          created_at: req.decided_at || req.created_at
        });
      } else if (req.status === 'CANCELLED') {
        const cancelledText = req.cancelled_after_approval
          ? `승인된 신청을 정상적으로 취소하였습니다: ${base}`
          : `접수하신 신청을 승인 전 취소하였습니다: ${base}`;
        events.push({
          id: `${req.id}-cancelled`,
          text: cancelledText,
          meta: reasonMeta,
          status: 'CANCELLED',
          created_at: req.decided_at || req.created_at
        });
      }
    } else {
      events.push({
        id: `${req.id}-submit`,
        text: `승인 대기 접수: ${applicant} - ${base}`,
        meta: reasonMeta,
        status: 'PENDING',
        created_at: req.created_at
      });
      if (req.status === 'APPROVED') {
        events.push({
          id: `${req.id}-approved`,
          text: `승인 완료: ${applicant} - ${base}`,
          meta: reasonMeta,
          status: 'APPROVED',
          created_at: req.decided_at || req.created_at
        });
      } else if (req.status === 'REJECTED') {
        events.push({
          id: `${req.id}-rejected`,
          text: `거부 처리됨: ${applicant} - ${base}`,
          meta: reasonMeta || '거부됨',
          status: 'REJECTED',
          created_at: req.decided_at || req.created_at
        });
      } else if (req.status === 'CANCELLED') {
        const cancelledText = req.cancelled_after_approval
          ? `승인 후 취소되었습니다: ${applicant} - ${base}`
          : `승인 전 취소 접수: ${applicant} - ${base}`;
        events.push({
          id: `${req.id}-cancelled`,
          text: cancelledText,
          meta: reasonMeta,
          status: 'CANCELLED',
          created_at: req.decided_at || req.created_at
        });
      }
    }
  });
  return events.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}

function buildFeedEvents(entries, userMap, shiftMap) {
  const events = [];
  entries.forEach((entry) => {
    const applicant = userMap[entry.user_id]?.name || '알 수 없는 신청자';
    const shift = shiftMap[entry.target_shift_id];
    const window = timeWindow(entry, shift);
    const kind = entry.type === 'ABSENCE' ? '결근' : '추가 근무';
    const base = `${entry.target_date} ${window ? `${window} ` : ''}${kind}`;
    const reasonMeta = entry.reason ? `사유: ${entry.reason}` : null;
    let status = 'PENDING';
    let text = `승인 대기 접수: ${applicant} - ${base}`;
    if (entry.action_type === 'REQUEST_APPROVE') {
      status = 'APPROVED';
      text = `승인 완료: ${applicant} - ${base}`;
    } else if (entry.action_type === 'REQUEST_REJECT') {
      status = 'REJECTED';
      text = `거부 처리됨: ${applicant} - ${base}`;
    } else if (entry.action_type === 'REQUEST_CANCEL') {
      status = 'CANCELLED';
      text = entry.cancelled_after_approval
        ? `승인 후 취소되었습니다: ${applicant} - ${base}`
        : `승인 전 취소 접수: ${applicant} - ${base}`;
    }
    events.push({
      id: `${entry.request_id}-${entry.action_type}`,
      text,
      meta: reasonMeta,
      status,
      created_at: entry.created_at
    });
  });
  return events.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}

async function fetchNotifications(user) {
  if (!user) return [];
  const { users, shifts } = await ensureMeta();
  const userMap = Object.fromEntries(users.map((u) => [u.id, u]));
  const shiftMap = Object.fromEntries(shifts.map((s) => [s.id, s]));
  if (user.role === 'MEMBER') {
    const source = await apiRequest('/requests/my');
    return buildRequestEvents(source, userMap, shiftMap, user.role).slice(0, 15);
  }
  const feed = await apiRequest('/requests/feed');
  return buildFeedEvents(feed, userMap, shiftMap).slice(0, 15);
}

export async function initNotifications(user) {
  const container = document.querySelector('.header-right') || document.querySelector('.mobile-actions') || document.querySelector('.mobile-top');
  if (!container) return;
  let panel = document.getElementById('notif-panel');
  let btn = document.getElementById('notif-btn');
  if (!btn) {
    btn = document.createElement('button');
    btn.id = 'notif-btn';
    btn.className = 'icon-btn bell';
    btn.title = '알림 보기';
    btn.innerHTML = `${bellSvg}<span id="notif-badge" class="notif-badge" style="display:none;"></span>`;
    container.insertBefore(btn, container.firstChild);
  }
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'notif-panel';
    panel.className = 'notif-panel';
    panel.innerHTML = `<div class="notif-header">알림 <button class="btn tiny muted" id="notif-refresh">새로고침</button></div><ul id="notif-list" class="notif-list"></ul>`;
    container.appendChild(panel);
  }

  const listEl = panel.querySelector('#notif-list');
  const badgeEl = document.getElementById('notif-badge');
  const lastSeenKey = user ? `notif_last_seen_${user.id}` : 'notif_last_seen';
  const maxItems = 10;
  let latest = [];

  function computeUnread(items) {
    const lastSeen = parseInt(localStorage.getItem(lastSeenKey) || '0', 10);
    return items.filter((i) => {
      const created = i.created_at ? Date.parse(i.created_at) : 0;
      return created > lastSeen;
    }).length;
  }

  async function refresh() {
    if (!listEl) return;
    listEl.innerHTML = '<li class="muted">불러오는 중...</li>';
    try {
      const items = await fetchNotifications(user);
      latest = items;
      listEl.innerHTML = '';
      if (!items.length) {
        listEl.innerHTML = '<li class="muted">새 알림이 없습니다</li>';
      } else {
        items.slice(0, maxItems).forEach((it) => listEl.appendChild(buildItem(it.text, it.status, it.meta)));
      }
      const unread = computeUnread(items);
      if (badgeEl) {
        badgeEl.textContent = unread;
        badgeEl.style.display = unread ? 'inline-block' : 'none';
      }
    } catch (e) {
      listEl.innerHTML = `<li class="error">알림을 불러오지 못했습니다: ${e.message || e}</li>`;
      if (badgeEl) badgeEl.style.display = 'none';
    }
  }

  panel.querySelector('#notif-refresh')?.addEventListener('click', (e) => {
    e.stopPropagation();
    refresh();
  });

  window.addEventListener('notifications:refresh', () => refresh());

  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    const willShow = !panel.classList.contains('show');
    panel.classList.toggle('show', willShow);
    if (willShow) {
      refresh().then(() => {
        const latestTime = latest.length ? Date.parse(latest[0].created_at || Date.now()) : Date.now();
        localStorage.setItem(lastSeenKey, latestTime.toString());
        if (badgeEl) badgeEl.style.display = 'none';
      });
    }
  });
  document.addEventListener('click', (e) => {
    if (!panel.contains(e.target) && e.target !== btn) panel.classList.remove('show');
  });

  await refresh();
}

export function triggerNotificationsRefresh() {
  const evt = new Event('notifications:refresh');
  window.dispatchEvent(evt);
}

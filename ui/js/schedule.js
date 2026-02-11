// File: /ui/js/schedule.js
import { apiRequest } from './api.js';

const days = ['월', '화', '수', '목', '금'];

function parseDateValue(dateStr) {
  if (!dateStr) return new Date();
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Date(y, (m || 1) - 1, d || 1);
}

function formatDateOnly(date) {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, '0');
  const d = String(date.getDate()).padStart(2, '0');
  return `${y}-${m}-${d}`;
}

function toMinutes(timeStr) {
  const [h, m] = timeStr.split(':').map(Number);
  return h * 60 + m;
}

function getWeekStart(dateStr) {
  const d = parseDateValue(dateStr);
  const diff = (d.getDay() + 6) % 7;
  const start = new Date(d);
  start.setDate(d.getDate() - diff);
  return formatDateOnly(start);
}

function nextWeekdayOnOrAfter(date, weekday) {
  const candidate = new Date(date);
  const delta = (weekday - candidate.getDay() + 7) % 7;
  candidate.setDate(candidate.getDate() + delta);
  return candidate;
}

function previousWeekdayOnOrBefore(date, weekday) {
  const candidate = new Date(date);
  const delta = (candidate.getDay() - weekday + 7) % 7;
  candidate.setDate(candidate.getDate() - delta);
  return candidate;
}

function getOccurrenceOnOrAfter(assignment, referenceDate) {
  if (!assignment?.valid_from || assignment.shift?.weekday === undefined) return null;
  const fromDate = parseDateValue(assignment.valid_from);
  const toDate = assignment.valid_to ? parseDateValue(assignment.valid_to) : null;
  const weekday = assignment.shift.weekday;
  const startDate = referenceDate > fromDate ? referenceDate : fromDate;
  const candidate = nextWeekdayOnOrAfter(startDate, weekday);
  if (toDate && candidate > toDate) return null;
  return candidate;
}

function getOccurrenceOnOrBefore(assignment, referenceDate) {
  if (!assignment?.valid_from || assignment.shift?.weekday === undefined) return null;
  const fromDate = parseDateValue(assignment.valid_from);
  const toDate = assignment.valid_to ? parseDateValue(assignment.valid_to) : null;
  const weekday = assignment.shift.weekday;
  const endDate = toDate && toDate < referenceDate ? toDate : referenceDate;
  const candidate = previousWeekdayOnOrBefore(endDate, weekday);
  if (candidate < fromDate) return null;
  return candidate;
}

function pickRelevantWeekStart(assignments = []) {
  if (!assignments.length) return null;
  const today = new Date();
  const todayOnly = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  let nextOccurrence = null;
  let lastOccurrence = null;

  assignments.forEach((assignment) => {
    const upcoming = getOccurrenceOnOrAfter(assignment, todayOnly);
    if (upcoming && (!nextOccurrence || upcoming < nextOccurrence)) {
      nextOccurrence = upcoming;
    }
    const previous = getOccurrenceOnOrBefore(assignment, todayOnly);
    if (previous && (!lastOccurrence || previous > lastOccurrence)) {
      lastOccurrence = previous;
    }
  });

  const chosen = nextOccurrence || lastOccurrence;
  if (chosen) return getWeekStart(formatDateOnly(chosen));
  const fallback = assignments.find((assignment) => assignment.valid_from);
  return fallback ? getWeekStart(fallback.valid_from) : null;
}

function normalizeEvents(assignments = []) {
  if (!assignments.length) return [];
  if (assignments[0].shift) return assignments;
  return assignments.map((ev) => {
    const weekday = (parseDateValue(ev.date).getDay() + 6) % 7;
    return {
      shift: {
        weekday,
        start_time: ev.start_time,
        end_time: ev.end_time,
        name: ev.shift_name || '',
      },
      user: { name: ev.user_name },
      source: ev.source || 'BASE',
    };
  });
}

function deriveHourWindow(assignments) {
  if (!assignments || assignments.length === 0) return { startHour: 9, endHour: 18 };
  const mins = assignments.flatMap((a) => [toMinutes(a.shift.start_time), toMinutes(a.shift.end_time)]);
  const startHour = Math.max(9, Math.floor(Math.min(...mins) / 60));
  const endHour = Math.min(18, Math.ceil(Math.max(...mins) / 60));
  if (startHour >= endHour) return { startHour: 9, endHour: 18 };
  return {
    startHour,
    endHour
  };
}

function createHeaderRow() {
  const header = document.createElement('div');
  header.className = 'timetable-header';
  header.appendChild(headerCell('시간'));
  days.forEach((day) => header.appendChild(headerCell(day)));
  return header;
}

function headerCell(text) {
  const div = document.createElement('div');
  div.className = 'day-label';
  div.textContent = text;
  return div;
}

function createTimelineColumns(hours) {
  const hourCount = hours.length;
  const times = document.createElement('div');
  times.className = 'timetable-times';
  times.style.setProperty('--hour-rows', hourCount);
  hours.forEach((h) => {
    const label = document.createElement('div');
    label.className = 'time-label';
    label.textContent = `${h}:00`;
    times.appendChild(label);
  });
  const dayCols = days.map(() => {
    const col = document.createElement('div');
    col.className = 'timetable-column';
    col.style.setProperty('--hour-rows', hourCount);
    hours.forEach((h) => {
      const cell = document.createElement('div');
      cell.className = 'timetable-cell';
      cell.dataset.hour = String(h);
      col.appendChild(cell);
    });
    return col;
  });
  return { times, dayCols };
}

function placeAssignments(dayCols, hours, assignments) {
  const colorPool = ['#1d4ed8', '#0f766e', '#b45309', '#be185d', '#7c3aed', '#2563eb'];
  const sourceColor = { EXTRA: '#0f766e', BASE: '#2563eb' };
  const userColor = new Map();

  const overlapsHour = (start, end, hour) => {
    const hourStart = hour * 60;
    const hourEnd = hourStart + 60;
    return end > hourStart && start < hourEnd;
  };

  assignments.forEach((assign) => {
    const dayIndex = assign.shift.weekday;
    const col = dayCols[dayIndex];
    if (!col) return;
    const startMinutes = toMinutes(assign.shift.start_time);
    const endMinutes = toMinutes(assign.shift.end_time);
    const memberName = assign.user?.name || assign.shift.name || '미정';
    const color = sourceColor[assign.source] || userColor.get(memberName) || colorPool[userColor.size % colorPool.length];
    if (!userColor.has(memberName)) userColor.set(memberName, color);

    hours.forEach((hour) => {
      if (!overlapsHour(startMinutes, endMinutes, hour)) return;
      const cell = col.querySelector(`.timetable-cell[data-hour="${hour}"]`);
      if (!cell) return;
      const pill = document.createElement('span');
      pill.className = 'timetable-pill';
      pill.textContent = memberName;
      pill.style.setProperty('--pill-color', color);
      if (assign.source === 'EXTRA') pill.classList.add('pill-extra');
      cell.appendChild(pill);
    });
  });

  dayCols.forEach((col) => {
    col.querySelectorAll('.timetable-cell').forEach((cell) => {
      if (!cell.childElementCount) {
        cell.classList.add('timetable-empty-slot');
        cell.textContent = '—';
      }
    });
  });
}

function syncRowHeights(hours, timesEl, dayCols, minHeight = 44) {
  if (!timesEl || !dayCols?.length || !hours?.length) return;
  const rowCount = hours.length;
  const heights = Array(rowCount).fill(minHeight);

  const collect = (container) => {
    const cells = Array.from(container.children).slice(0, rowCount);
    cells.forEach((cell, idx) => {
      const cellHeight = Math.ceil(cell.scrollHeight || cell.offsetHeight || minHeight);
      heights[idx] = Math.max(heights[idx], cellHeight);
    });
  };

  collect(timesEl);
  dayCols.forEach((col) => collect(col));

  const rowTemplate = heights.map((h) => `${Math.max(minHeight, h)}px`).join(' ');
  const totalHeight = heights.reduce((a, b) => a + b, 0);
  timesEl.style.gridTemplateRows = rowTemplate;
  timesEl.style.height = `${totalHeight}px`;
  dayCols.forEach((col) => {
    col.style.gridTemplateRows = rowTemplate;
    col.style.height = `${totalHeight}px`;
  });
}

function renderTimeline(assignments, targetId, { hourHeight = 44 } = {}) {
  const container = document.getElementById(targetId);
  if (!container) return;
  if (container._timetableCleanup) {
    container._timetableCleanup();
    container._timetableCleanup = null;
  }
  container.innerHTML = '';
  const normalized = normalizeEvents(assignments);
  if (!assignments || assignments.length === 0) {
    container.classList.remove('timetable');
    container.textContent = '배정된 일정이 없습니다.';
    return;
  }

  const { startHour, endHour } = deriveHourWindow(normalized);
  const hours = Array.from({ length: endHour - startHour }, (_, i) => startHour + i);
  const wrapper = document.createElement('div');
  wrapper.className = 'timetable';
  wrapper.style.setProperty('--hour-rows', hours.length);
  wrapper.style.setProperty('--hour-height', `${hourHeight}px`);

  const header = createHeaderRow();
  const body = document.createElement('div');
  body.className = 'timetable-body';
  body.style.setProperty('--hour-rows', hours.length);
  body.style.setProperty('--hour-height', `${hourHeight}px`);

  const { times, dayCols } = createTimelineColumns(hours);
  placeAssignments(dayCols, hours, normalized);
  const recalcHeights = () => syncRowHeights(hours, times, dayCols, hourHeight);
  recalcHeights();
  requestAnimationFrame(recalcHeights);

  body.appendChild(times);
  dayCols.forEach((col) => body.appendChild(col));

  wrapper.appendChild(header);
  wrapper.appendChild(body);
  container.appendChild(wrapper);

  const resizeObserver = new ResizeObserver(() => recalcHeights());
  resizeObserver.observe(wrapper);
  const resizeHandler = () => recalcHeights();
  window.addEventListener('resize', resizeHandler);
  container._timetableCleanup = () => {
    window.removeEventListener('resize', resizeHandler);
    resizeObserver.disconnect();
  };
}

async function loadGlobalSchedule(targetId = 'schedule-container', options = {}) {
  const start = getWeekStart();
  const params = new URLSearchParams({ start });
  let events = await apiRequest(`/schedule/weekly_view?${params.toString()}`);
  if (!events.length) {
    const snapshot = await apiRequest('/schedule/global');
    const assignments = snapshot?.assignments || [];
    const fallbackStart = pickRelevantWeekStart(assignments);
    if (fallbackStart && fallbackStart !== start) {
      const fallbackParams = new URLSearchParams({ start: fallbackStart });
      events = await apiRequest(`/schedule/weekly_view?${fallbackParams.toString()}`);
    }
  }
  renderTimeline(events, targetId, options);
  return events;
}

async function loadBaseSchedule(targetId = 'schedule-container', options = {}) {
  const start = getWeekStart();
  const params = new URLSearchParams({ start });
  let events = await apiRequest(`/schedule/weekly_base?${params.toString()}`);
  if (!events.length) {
    const snapshot = await apiRequest('/schedule/global');
    const assignments = snapshot?.assignments || [];
    const fallbackStart = pickRelevantWeekStart(assignments);
    if (fallbackStart && fallbackStart !== start) {
      const fallbackParams = new URLSearchParams({ start: fallbackStart });
      events = await apiRequest(`/schedule/weekly_base?${fallbackParams.toString()}`);
    }
  }
  renderTimeline(events, targetId, options);
  return events;
}

function renderCompactSchedule(assignments, targetId = 'schedule-summary', options = {}) {
  const { hourHeight = 38 } = options;
  renderTimeline(assignments, targetId, { hourHeight });
}

async function loadMySchedule(user) {
  const listEl = document.getElementById('my-schedule');
  if (!listEl) return;
  listEl.classList.add('schedule-list');
  const data = await apiRequest('/schedule/global');
  const assignments = data?.assignments || [];
  const mine = assignments.filter((assignment) => assignment.user?.id === user?.id);
  listEl.innerHTML = '';
  if (!mine.length) {
    const li = document.createElement('li');
    li.className = 'muted';
    li.textContent = '이번 주 배정된 근무가 없습니다.';
    listEl.appendChild(li);
    return;
  }
  const dayNames = ['월', '화', '수', '목', '금', '토', '일'];
  const items = [];
  mine.forEach((assignment) => {
    if (!assignment.shift) return;
    const weekday = dayNames[assignment.shift.weekday] || '-';
    const timeText = `${assignment.shift.start_time.slice(0, 5)}~${assignment.shift.end_time.slice(0, 5)}`;
    items.push({ key: `${weekday}-${timeText}`, weekday, timeText });
  });
  const seen = new Set();
  items.forEach((item) => {
    if (seen.has(item.key)) return;
    seen.add(item.key);
    const li = document.createElement('li');
    li.className = 'schedule-item';
    li.innerHTML = `
      <span class="schedule-date">${item.weekday}</span>
      <span class="schedule-time">${item.timeText}</span>
    `;
    listEl.appendChild(li);
  });
}

export { loadBaseSchedule, loadGlobalSchedule, loadMySchedule, renderCompactSchedule };

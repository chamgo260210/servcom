// File: /ui/js/visitors.js
import { apiRequest } from './api.js';

const periodTypes = {
  SEMESTER_1: '1학기',
  SEMESTER_2: '2학기',
  SUMMER_BREAK: '여름방학',
  WINTER_BREAK: '겨울방학'
};

const periodFields = [
  { type: 'SEMESTER_1', start: 'period-semester1-start', end: 'period-semester1-end' },
  { type: 'SUMMER_BREAK', start: 'period-summer-start', end: 'period-summer-end' },
  { type: 'SEMESTER_2', start: 'period-semester2-start', end: 'period-semester2-end' },
  { type: 'WINTER_BREAK', start: 'period-winter-start', end: 'period-winter-end' }
];

let currentYear = null;
let entries = [];
let entriesByDate = new Map();
let entriesByMonth = new Map();
let periods = [];
let selectedEntryId = null;
let calendarCursor = null;
let entryMonthCursor = null;
let pendingEntryMonth = null;
let bulkMonthCursor = null;
let isPeriodDraftMode = false;

function formatNumber(value) {
  if (value === null || value === undefined) return '-';
  return Number(value).toLocaleString('ko-KR');
}

function parseNumberInput(value) {
  if (value === null || value === undefined) return null;
  if (value === '') return null;
  const parsed = Number.parseInt(value, 10);
  return Number.isNaN(parsed) ? null : parsed;
}

function parseDateInput(value) {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function formatDateKey(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${year}-${month}-${day}`;
}

function formatMonthKey(date) {
  if (!date) return '';
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  return `${year}-${month}`;
}

function setFormMessage(elementId, message) {
  const el = getElement(elementId);
  if (!el) return;
  if (message) {
    el.textContent = message;
    el.classList.add('show');
  } else {
    el.textContent = '';
    el.classList.remove('show');
  }
}

function showUserError(message, elementId) {
  if (!message) return;
  setFormMessage(elementId, message);
  alert(message);
}

function formatDate(dateStr) {
  if (!dateStr) return '-';
  try {
    return new Date(dateStr).toLocaleDateString('ko-KR');
  } catch {
    return dateStr;
  }
}

function formatDateRange(start, end) {
  if (!start || !end) return '-';
  return `${formatDate(start)} ~ ${formatDate(end)}`;
}

function formatDateTime(dateStr) {
  if (!dateStr) return '-';
  try {
    return new Date(dateStr).toLocaleString('ko-KR');
  } catch {
    return dateStr;
  }
}
function getElement(id) {
  return document.getElementById(id);
}

function resetEntryForm() {
  selectedEntryId = null;
  const visitDate = getElement('visit-date');
  if (visitDate) {
    visitDate.value = resolveDefaultVisitDate();
    visitDate.disabled = true;
  }
  const count1 = getElement('count1');
  const count2 = getElement('count2');
  if (count1) count1.value = '';
  if (count2) count2.value = '';
  const prevTotal = getElement('prev-total');
  if (prevTotal) prevTotal.value = '';
  const deleteBtn = getElement('delete-entry');
  const todayKey = resolveDefaultVisitDate();
  const todayMonthKey = todayKey ? formatMonthKey(new Date(todayKey)) : '';
  let todayEntry = todayKey ? entriesByDate.get(todayKey) : null;
  if (!todayEntry && todayMonthKey && entriesByMonth.has(todayMonthKey)) {
    todayEntry = entriesByMonth.get(todayMonthKey).find((item) => item.visit_date === todayKey);
  }
  if (deleteBtn) {
    if (todayEntry) {
      selectedEntryId = todayEntry.id;
      deleteBtn.style.display = '';
    } else {
      deleteBtn.style.display = 'none';
    }
  }
  setFormMessage('entry-message', '');
  updateEntryPreview();
}

function resetBulkEntryForm() {
  selectedEntryId = null;
  const visitDate = getElement('bulk-visit-date');
  if (visitDate) {
    const today = new Date();
    const yesterday = new Date(today.getFullYear(), today.getMonth(), today.getDate() - 1);
    let defaultDate = yesterday;
    if (currentYear) {
      const yearStart = new Date(currentYear.start_date);
      const yearEnd = new Date(currentYear.end_date);
      if (defaultDate < yearStart) defaultDate = yearStart;
      if (defaultDate > yearEnd) defaultDate = yearEnd;
    }
    visitDate.max = formatDateKey(yesterday);
    visitDate.value = formatDateKey(defaultDate);
  }
  const dailyInput = getElement('bulk-daily-visitors');
  if (dailyInput) dailyInput.value = '';
  const deleteBtn = getElement('delete-bulk-entry');
  if (deleteBtn) deleteBtn.style.display = 'none';
  setFormMessage('bulk-entry-message', '');
  updateBulkEntryPreview();
}

function resolveBulkMonthCursor() {
  if (bulkMonthCursor) return bulkMonthCursor;
  const monthInput = getElement('bulk-month')?.value;
  if (monthInput) {
    const [year, month] = monthInput.split('-').map(Number);
    if (year && month) return new Date(year, month - 1, 1);
  }
  return new Date(resolveDefaultVisitDate());
}

function renderBulkMonthTable() {
  const tbody = getElement('bulk-month-table')?.querySelector('tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  if (!currentYear) return;
  const cursor = resolveBulkMonthCursor();
  bulkMonthCursor = new Date(cursor.getFullYear(), cursor.getMonth(), 1);
  const monthInput = getElement('bulk-month');
  if (monthInput) {
    monthInput.value = `${bulkMonthCursor.getFullYear()}-${String(bulkMonthCursor.getMonth() + 1).padStart(2, '0')}`;
  }
  const startDate = new Date(bulkMonthCursor.getFullYear(), bulkMonthCursor.getMonth(), 1);
  const endDate = new Date(bulkMonthCursor.getFullYear(), bulkMonthCursor.getMonth() + 1, 0);
  const yearStart = parseDateInput(currentYear.start_date);
  const yearEnd = parseDateInput(currentYear.end_date);
  const start = startDate < yearStart ? new Date(yearStart) : startDate;
  const end = endDate > yearEnd ? new Date(yearEnd) : endDate;
  const current = new Date(start.getFullYear(), start.getMonth(), start.getDate());
  const todayKey = formatDateKey(new Date());
  while (current <= end) {
    const dayOfWeek = current.getDay();
    if (dayOfWeek === 0 || dayOfWeek === 6) {
      current.setDate(current.getDate() + 1);
      continue;
    }
    const dateStr = formatDateKey(current);
    if (dateStr >= todayKey) {
      current.setDate(current.getDate() + 1);
      continue;
    }
    const entry = entriesByDate.get(dateStr);
    const value = entry?.daily_visitors ?? '';
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${formatDate(dateStr)}</td>
      <td><input type="number" min="0" class="bulk-month-input" data-date="${dateStr}" data-original="${value ?? ''}" value="${value ?? ''}" /></td>
    `;
    tbody.appendChild(tr);
    current.setDate(current.getDate() + 1);
  }
}

function updateResetControls() {
  const monthInput = getElement('reset-month');
  if (!monthInput) return;
  const today = new Date();
  monthInput.value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}`;
}

function formatMonthLabel(dateObj) {
  if (!dateObj) return '-';
  return `${dateObj.getFullYear()}년 ${dateObj.getMonth() + 1}월`;
}

function updateBulkEntryAvailability() {
  const hint = getElement('bulk-entry-hint');
  if (hint) {
    hint.textContent = '과거 날짜의 일일 방문자 수만 입력할 수 있습니다. 오늘 날짜는 일일 입력을 이용하세요.';
  }
}

function updateYearSummary() {
  const label = getElement('year-label');
  const academic = getElement('year-academic');
  const range = getElement('year-range');
  if (!currentYear) {
    if (label) label.textContent = '-';
    if (academic) academic.textContent = '-';
    if (range) range.textContent = '-';
    return;
  }
  if (label) label.textContent = currentYear.label;
  if (academic) academic.textContent = `${currentYear.academic_year}학년도`;
  if (range) range.textContent = formatDateRange(currentYear.start_date, currentYear.end_date);
}

async function ensureEntriesForMonth(dateObj, { setActive = false } = {}) {
  if (!currentYear || !dateObj) return [];
  const monthKey = formatMonthKey(dateObj);
  if (!monthKey) return [];
  if (entriesByMonth.has(monthKey)) {
    const cached = entriesByMonth.get(monthKey) || [];
    if (setActive) {
      entries = cached;
      entriesByDate = new Map(cached.map((entry) => [entry.visit_date, entry]));
    }
    return cached;
  }
  const data = await apiRequest(`/visitors/years/${currentYear.id}/entries?month=${monthKey}`);
  const list = data || [];
  entriesByMonth.set(monthKey, list);
  if (setActive) {
    entries = list;
    entriesByDate = new Map(list.map((entry) => [entry.visit_date, entry]));
  }
  return list;
}

async function renderEntries() {
  const tbody = getElement('entry-table')?.querySelector('tbody');
  const status = getElement('entry-status');
  const label = getElement('entry-month-label');
  if (!tbody) return;
  const cursor = resolveEntryMonthCursor();
  if (label) label.textContent = formatMonthLabel(cursor);
  tbody.innerHTML = '';
  await ensureEntriesForMonth(cursor, { setActive: true });
  if (!entries.length) {
    if (status) status.textContent = '아직 기록된 데이터가 없습니다.';
    return;
  }
  const filtered = entries.filter((entry) => {
    const entryDate = new Date(entry.visit_date);
    return entryDate.getFullYear() === cursor.getFullYear() && entryDate.getMonth() === cursor.getMonth();
  });
  if (status) status.textContent = `${formatMonthLabel(cursor)} · ${filtered.length}건`;
  filtered.forEach((entry) => {
    const tr = document.createElement('tr');
    if (entry.id === selectedEntryId) tr.classList.add('selected');
    const updater = entry.updated_by_name || entry.created_by_name || '-';
    const totalCount = entry.total_count === 0 && entry.daily_override !== null && entry.previous_total !== null
      ? entry.previous_total + entry.daily_override
      : entry.total_count;
    const dailyVisitors = entry.daily_visitors === 0 && entry.daily_override !== null
      ? entry.daily_override
      : entry.daily_visitors;
    tr.innerHTML = `
      <td>${formatDate(entry.visit_date)}</td>
      <td>${formatNumber(entry.daily_visitors)}</td>
      <td>${updater}</td>
      <td>${formatDateTime(entry.updated_at)}</td>
    `;
    tr.addEventListener('click', () => selectEntry(entry));
    tbody.appendChild(tr);
  });
}

function renderMonthly(summary) {
  const tbody = getElement('monthly-table')?.querySelector('tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  if (!summary?.monthly?.length) {
    tbody.innerHTML = '<tr><td colspan="3" class="muted">표시할 데이터가 없습니다.</td></tr>';
    return;
  }
  summary.monthly.forEach((month) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${month.label}</td>
      <td>${formatNumber(month.open_days)}</td>
      <td>${formatNumber(month.total_visitors)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderPeriodStats(summary) {
  const tbody = getElement('period-table')?.querySelector('tbody');
  if (!tbody) return;
  tbody.innerHTML = '';
  if (!summary?.periods?.length) {
    tbody.innerHTML = '<tr><td colspan="4" class="muted">표시할 데이터가 없습니다.</td></tr>';
    return;
  }
  summary.periods.forEach((period) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${period.name}</td>
      <td>${formatDateRange(period.start_date, period.end_date)}</td>
      <td>${formatNumber(period.open_days)}</td>
      <td>${formatNumber(period.total_visitors)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function updateSummary(summary) {
  const totalDays = getElement('total-days');
  const totalVisitors = getElement('total-visitors');
  if (totalDays) totalDays.textContent = formatNumber(summary?.open_days ?? 0);
  if (totalVisitors) totalVisitors.textContent = formatNumber(summary?.total_visitors ?? 0);
}

function isDateWithinYear(year, targetDate) {
  if (!year || !targetDate) return false;
  const start = new Date(year.start_date);
  const end = new Date(year.end_date);
  return targetDate >= start && targetDate <= end;
}

function resolveDefaultVisitDate() {
  if (!currentYear) return '';
  const today = new Date();
  if (isDateWithinYear(currentYear, today)) {
    return today.toISOString().slice(0, 10);
  }
  return '';
}

function resolveCalendarCursor() {
  if (calendarCursor) return calendarCursor;
  const today = new Date();
  if (currentYear) {
    const start = new Date(currentYear.start_date);
    const end = new Date(currentYear.end_date);
    if (today >= start && today <= end) {
      calendarCursor = new Date(today.getFullYear(), today.getMonth(), 1);
      return calendarCursor;
    }
    calendarCursor = new Date(start.getFullYear(), start.getMonth(), 1);
    return calendarCursor;
  }
  calendarCursor = new Date(today.getFullYear(), today.getMonth(), 1);
  return calendarCursor;
}

function resolveEntryMonthCursor() {
  if (entryMonthCursor) return entryMonthCursor;
  const today = new Date();
  if (currentYear && isDateWithinYear(currentYear, today)) {
    entryMonthCursor = new Date(today.getFullYear(), today.getMonth(), 1);
    return entryMonthCursor;
  }
  if (entries.length) {
    const firstEntry = entries[0];
    const dateObj = new Date(firstEntry.visit_date);
    entryMonthCursor = new Date(dateObj.getFullYear(), dateObj.getMonth(), 1);
    return entryMonthCursor;
  }
  if (currentYear) {
    const start = new Date(currentYear.start_date);
    entryMonthCursor = new Date(start.getFullYear(), start.getMonth(), 1);
    return entryMonthCursor;
  }
  entryMonthCursor = new Date(today.getFullYear(), today.getMonth(), 1);
  return entryMonthCursor;
}

function toMonthStart(dateObj) {
  if (!dateObj) return null;
  return new Date(dateObj.getFullYear(), dateObj.getMonth(), 1);
}

async function moveEntryMonth(monthOffset) {
  const cursor = resolveEntryMonthCursor();
  let next = new Date(cursor.getFullYear(), cursor.getMonth() + monthOffset, 1);
  if (currentYear) {
    const start = new Date(currentYear.start_date);
    const end = new Date(currentYear.end_date);
    const startMonth = new Date(start.getFullYear(), start.getMonth(), 1);
    const endMonth = new Date(end.getFullYear(), end.getMonth(), 1);
    if (next < startMonth) {
      next = startMonth;
    } else if (next > endMonth) {
      next = endMonth;
    }
  }
  entryMonthCursor = next;
  await renderEntries();
}

function buildEntriesMap() {
  return entriesByDate;
}

async function renderCalendar() {
  const grid = getElement('calendar-grid');
  const label = getElement('calendar-label');
  const meta = getElement('calendar-meta');
  if (!grid || !label || !meta) return;
  if (!currentYear) {
    grid.innerHTML = '';
    label.textContent = '-';
    meta.textContent = '-';
    return;
  }
  const cursor = resolveCalendarCursor();
  const year = cursor.getFullYear();
  const month = cursor.getMonth();
  label.textContent = `${year}년 ${month + 1}월`;
  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);
  const startWeekday = firstDay.getDay();
  const totalDays = lastDay.getDate();
  const monthEntries = await ensureEntriesForMonth(cursor);
  const entriesMap = new Map(monthEntries.map((entry) => [entry.visit_date, entry]));
  let monthTotal = 0;
  let openDays = 0;
  monthEntries.forEach((entry) => {
    const entryDate = new Date(entry.visit_date);
    if (entryDate.getFullYear() === year && entryDate.getMonth() === month) {
      monthTotal += entry.daily_visitors;
      openDays += 1;
    }
  });
  meta.textContent = `개관일수 ${formatNumber(openDays)}일 · 출입자 ${formatNumber(monthTotal)}명`;
  grid.innerHTML = '';
  for (let i = 0; i < startWeekday; i += 1) {
    const cell = document.createElement('div');
    cell.className = 'calendar-cell muted';
    cell.textContent = '';
    grid.appendChild(cell);
  }
  for (let day = 1; day <= totalDays; day += 1) {
    const cell = document.createElement('div');
    const dateStr = formatDateKey(new Date(year, month, day));
    const entry = entriesMap.get(dateStr);
    const author = entry?.updated_by_name || entry?.created_by_name || '-';
    const isToday = dateStr === formatDateKey(new Date());
    cell.className = 'calendar-cell';
    if (isToday) cell.classList.add('is-today');
    cell.innerHTML = `
      <div class="calendar-date">${day}</div>
      <div class="calendar-value">${entry ? formatNumber(entry.daily_visitors) : '-'}</div>
      <div class="calendar-author">${entry ? author : ''}</div>
    `;
    if (entry) {
      cell.classList.add('has-entry');
      cell.title = `${formatDate(dateStr)}: ${formatNumber(entry.daily_visitors)}명 (${author})`;
      cell.addEventListener('click', () => selectEntry(entry));
    }
    grid.appendChild(cell);
  }
}

async function moveCalendar(monthOffset) {
  const cursor = resolveCalendarCursor();
  const next = new Date(cursor.getFullYear(), cursor.getMonth() + monthOffset, 1);
  if (currentYear) {
    const start = new Date(currentYear.start_date);
    const end = new Date(currentYear.end_date);
    const startMonth = new Date(start.getFullYear(), start.getMonth(), 1);
    const endMonth = new Date(end.getFullYear(), end.getMonth(), 1);
    if (next < startMonth) {
      calendarCursor = startMonth;
    } else if (next > endMonth) {
      calendarCursor = endMonth;
    } else {
      calendarCursor = next;
    }
  } else {
    calendarCursor = next;
  }
  await renderCalendar();
}

function updatePeriodForm() {
  periods.forEach((period) => {
    const fields = periodFields.find((item) => item.type === period.period_type);
    if (!fields) return;
    const startEl = getElement(fields.start);
    const endEl = getElement(fields.end);
    if (startEl) startEl.value = period.start_date || '';
    if (endEl) endEl.value = period.end_date || '';
  });
}

function setPeriodFormEditable(isEditable) {
  periodFields.forEach((field) => {
    const startEl = getElement(field.start);
    const endEl = getElement(field.end);
    if (startEl) startEl.disabled = !isEditable;
    if (endEl) endEl.disabled = !isEditable;
  });
}

function resetPeriodDraft() {
  periodFields.forEach((field) => {
    const startEl = getElement(field.start);
    const endEl = getElement(field.end);
    if (startEl) startEl.value = '';
    if (endEl) endEl.value = '';
  });
}

function selectEntry(entry) {
  selectedEntryId = entry.id;
  const entryDate = new Date(entry.visit_date);
  entryMonthCursor = new Date(entryDate.getFullYear(), entryDate.getMonth(), 1);

  const bulkVisit = getElement('bulk-visit-date');
  if (bulkVisit) bulkVisit.value = entry.visit_date;
  const bulkDaily = getElement('bulk-daily-visitors');
  if (bulkDaily) bulkDaily.value = entry.daily_visitors ?? '';
  const bulkDelete = getElement('delete-bulk-entry');
  if (bulkDelete) bulkDelete.style.display = '';

  updateEntryPreview(entry);
  updateBulkEntryPreview(entry);
  renderEntries();
}

function updateEntryPreview(entry) {
  if (!getElement('preview-prev-total')) return;
  const count1 = parseNumberInput(getElement('count1')?.value);
  const count2 = parseNumberInput(getElement('count2')?.value);
  const prevTotal = parseNumberInput(getElement('prev-total')?.value);
  const hasCounts = count1 !== null && count2 !== null;
  const total = hasCounts ? (count1 || 0) + (count2 || 0) : null;
  const daily = total !== null && prevTotal !== null ? total - prevTotal : null;
  const prevEl = getElement('preview-prev-total');
  const totalEl = getElement('preview-total');
  const dailyEl = getElement('preview-daily');
  const updaterEl = getElement('preview-updater');
  if (prevEl) prevEl.textContent = formatNumber(prevTotal);
  if (totalEl) totalEl.textContent = formatNumber(total);
  if (dailyEl) dailyEl.textContent = formatNumber(daily);
  if (updaterEl) updaterEl.textContent = entry?.updated_by_name || entry?.created_by_name || '-';
}

function updateTodayEntryCard() {
  const status = getElement('today-entry-status');
  const summary = getElement('today-entry-summary');
  if (!status || !summary) return;
  const todayKey = formatDateKey(new Date());
  const todayMonthKey = formatMonthKey(new Date());
  let entry = entriesByDate.get(todayKey);
  if (!entry && entriesByMonth.has(todayMonthKey)) {
    entry = entriesByMonth.get(todayMonthKey).find((item) => item.visit_date === todayKey);
  }
  if (!entry) {
    status.textContent = '오늘 기록이 없습니다.';
    summary.style.display = 'none';
    return;
  }
  status.textContent = '오늘 기록을 확인하세요.';
  summary.style.display = '';
  const dateEl = getElement('today-entry-date');
  const visitorsEl = getElement('today-entry-visitors');
  const updaterEl = getElement('today-entry-updater');
  const updatedEl = getElement('today-entry-updated');
  if (dateEl) dateEl.textContent = formatDate(entry.visit_date);
  if (visitorsEl) visitorsEl.textContent = formatNumber(entry.daily_visitors);
  if (updaterEl) updaterEl.textContent = entry.updated_by_name || entry.created_by_name || '-';
  if (updatedEl) updatedEl.textContent = formatDateTime(entry.updated_at);
}

function updateBulkEntryPreview(entry) {
  if (!getElement('bulk-preview-daily')) return;
  const dailyInput = parseNumberInput(getElement('bulk-daily-visitors')?.value);
  const dailyEl = getElement('bulk-preview-daily');
  const updaterEl = getElement('bulk-preview-updater');
  if (dailyEl) dailyEl.textContent = formatNumber(dailyInput ?? entry?.daily_visitors ?? null);
  if (updaterEl) updaterEl.textContent = entry?.updated_by_name || entry?.created_by_name || '-';
}

async function loadPreviousTotal() {
  if (!currentYear) return;
  try {
    const data = await apiRequest(`/visitors/years/${currentYear.id}/running-total/load`, {
      method: 'POST'
    });
    const prevTotal = getElement('prev-total');
    if (prevTotal) {
      if (data.previous_total === null || data.previous_total === undefined) {
        showUserError('전일 합산 정보가 없습니다. 직접 입력하세요.', 'entry-message');
        prevTotal.focus();
      } else {
        prevTotal.value = String(data.previous_total);
        setFormMessage('entry-message', '');
        updateEntryPreview();
      }
    }
  } catch (error) {
    showUserError(error.message || '전일 합산 불러오기에 실패했습니다.', 'entry-message');
  }
}

async function loadYearDetail(yearId) {
  if (!yearId) return;
  const data = await apiRequest(`/visitors/years/${yearId}`);
  currentYear = data.year;
  entries = [];
  entriesByDate = new Map();
  entriesByMonth = new Map();
  periods = data.periods || [];
  calendarCursor = null;
  if (pendingEntryMonth) {
    entryMonthCursor = toMonthStart(pendingEntryMonth);
    pendingEntryMonth = null;
  } else {
    entryMonthCursor = null;
  }
  updateYearSummary();
  await renderEntries();
  updateSummary(data.summary);
  renderMonthly(data.summary);
  renderPeriodStats(data.summary);
  updatePeriodForm();
  isPeriodDraftMode = false;
  setPeriodFormEditable(false);
  const today = new Date();
  if (currentYear && isDateWithinYear(currentYear, today)) {
    await ensureEntriesForMonth(today);
  }
  resetEntryForm();
  resetBulkEntryForm();
  updateTodayEntryCard();
  updateBulkEntryAvailability();
  renderBulkMonthTable();
  updateResetControls();
  await renderCalendar();
}

function resolveYearByDate(years, targetDate) {
  if (!targetDate) return null;
  return years.find((year) => isDateWithinYear(year, targetDate)) || null;
}

async function loadYears(preferredAcademicYear = null) {
  const select = getElement('year-select');
  const years = await apiRequest('/visitors/years');
  if (select) {
    select.innerHTML = '';
    years.forEach((year) => {
      const option = document.createElement('option');
      option.value = year.id;
      option.textContent = year.label;
      select.appendChild(option);
    });
  }
  if (years.length) {
    const preferred = preferredAcademicYear
      ? years.find((year) => year.academic_year === preferredAcademicYear)
      : null;
    const today = new Date();
    const datedYear = resolveYearByDate(years, today);
    const activeYear = preferred || datedYear || years[0];
    if (select) {
      select.value = activeYear.id;
    }
    await loadYearDetail(activeYear.id);
  } else {
    currentYear = null;
    entries = [];
    entriesByDate = new Map();
    entriesByMonth = new Map();
    periods = [];
    entryMonthCursor = null;
    updateYearSummary();
    await renderEntries();
    updateSummary({ open_days: 0, total_visitors: 0, monthly: [], periods: [] });
    renderMonthly({ monthly: [] });
    renderPeriodStats({ periods: [] });
    await renderCalendar();
    resetEntryForm();
    resetBulkEntryForm();
    updateTodayEntryCard();
    isPeriodDraftMode = false;
    setPeriodFormEditable(false);
  }
}

function bindEvents() {
  getElement('year-select')?.addEventListener('change', async (event) => {
    const value = event.target.value;
    try {
      await loadYearDetail(value);
    } catch (error) {
      console.error(error);
      showUserError(error.message || '학년도 정보를 불러오지 못했습니다.', 'entry-message');
    }
  });

  getElement('apply-year')?.addEventListener('click', async () => {
    const yearInput = parseInt(getElement('new-year-input')?.value || '0', 10);
    if (!yearInput) {
      showUserError('학년도 숫자를 입력하세요.', 'entry-message');
      return;
    }
    const periodPayload = periodFields.map((field) => ({
      period_type: field.type,
      name: periodTypes[field.type],
      start_date: getElement(field.start)?.value || null,
      end_date: getElement(field.end)?.value || null
    }));
    try {
      await apiRequest('/visitors/years', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ academic_year: yearInput, periods: periodPayload })
      });
    } catch (error) {
      if (String(error.message).includes('이미 등록된 학년도')) {
        showUserError('이미 등록된 학년도입니다.', 'entry-message');
        return;
      }
      showUserError(error.message || '학년도 생성에 실패했습니다.', 'entry-message');
      return;
    }
    getElement('new-year-input').value = '';
    isPeriodDraftMode = false;
    setPeriodFormEditable(false);
    await loadYears(yearInput);
  });

  getElement('new-year-input')?.addEventListener('input', (event) => {
    const value = event.target.value.trim();
    if (value) {
      if (!isPeriodDraftMode) {
        resetPeriodDraft();
      }
      isPeriodDraftMode = true;
      setPeriodFormEditable(true);
    } else {
      isPeriodDraftMode = false;
      setPeriodFormEditable(false);
      updatePeriodForm();
    }
  });

  getElement('save-entry')?.addEventListener('click', async () => {
    if (!currentYear) return;
    const visitDate = getElement('visit-date')?.value;
    if (!visitDate) {
      showUserError('날짜를 선택하세요.', 'entry-message');
      return;
    }
    const today = new Date().toISOString().slice(0, 10);
    if (visitDate !== today) {
      showUserError('일일 입력은 오늘 날짜만 가능합니다.', 'entry-message');
      return;
    }
    const count1 = parseNumberInput(getElement('count1')?.value);
    const count2 = parseNumberInput(getElement('count2')?.value);
    const prevTotal = parseNumberInput(getElement('prev-total')?.value);
    if (count1 !== null && (count1 < 0 || count1 > 1000000)) {
      showUserError('Count 1은 0 이상 1,000,000 이하만 입력할 수 있습니다.', 'entry-message');
      return;
    }
    if (count2 !== null && (count2 < 0 || count2 > 1000000)) {
      showUserError('Count 2는 0 이상 1,000,000 이하만 입력할 수 있습니다.', 'entry-message');
      return;
    }
    if (count1 === null && count2 === null) {
      showUserError('Count 1과 Count 2를 입력하세요.', 'entry-message');
      return;
    }
    if (count1 === null || count2 === null) {
      showUserError('Count 1과 Count 2를 모두 입력하세요.', 'entry-message');
      return;
    }
    if (prevTotal === null) {
      showUserError('전일 합산을 불러오거나 직접 입력하세요.', 'entry-message');
      return;
    }
    if (prevTotal < 0 || prevTotal > 100000000) {
      showUserError('전일 합산은 0 이상 100,000,000 이하만 입력할 수 있습니다.', 'entry-message');
      return;
    }
    const totalCount = (count1 || 0) + (count2 || 0);
    const dailyVisitors = totalCount - prevTotal;
    if (dailyVisitors < 0) {
      showUserError('금일 합산이 전일 합산보다 작습니다. 전일 합산을 확인하세요.', 'entry-message');
      return;
    }
    setFormMessage('entry-message', '');
    try {
      pendingEntryMonth = new Date(visitDate);
      await apiRequest(`/visitors/years/${currentYear.id}/entries`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          visit_date: visitDate,
          daily_visitors: dailyVisitors,
          previous_total: totalCount
        })
      });
      await loadYearDetail(currentYear.id);
      resetEntryForm();
    } catch (error) {
      showUserError(error.message || '저장에 실패했습니다.', 'entry-message');
    }
  });

  getElement('reset-entry')?.addEventListener('click', resetEntryForm);

  getElement('delete-entry')?.addEventListener('click', async () => {
    if (!currentYear || !selectedEntryId) return;
    if (!confirm('선택한 기록을 삭제할까요?')) return;
    try {
      await apiRequest(`/visitors/years/${currentYear.id}/entries/${selectedEntryId}`, {
        method: 'DELETE'
      });
      await loadYearDetail(currentYear.id);
      resetEntryForm();
    } catch (error) {
      showUserError(error.message || '삭제에 실패했습니다.', 'entry-message');
    }
  });

  getElement('save-bulk-entry')?.addEventListener('click', async () => {
    if (!currentYear) return;
    const visitDate = getElement('bulk-visit-date')?.value;
    if (!visitDate) {
      showUserError('날짜를 선택하세요.', 'bulk-entry-message');
      return;
    }
    const dailyVisitors = parseNumberInput(getElement('bulk-daily-visitors')?.value);
    const today = new Date().toISOString().slice(0, 10);
    if (visitDate >= today) {
      showUserError('오늘 날짜는 일일 입력에서만 가능합니다.', 'bulk-entry-message');
      return;
    }
    if (dailyVisitors === null) {
      showUserError('일일 방문자 수를 입력하세요.', 'bulk-entry-message');
      return;
    }
    if (dailyVisitors < 0 || dailyVisitors > 1000000) {
      showUserError('금일 출입자는 0 이상 1,000,000 이하만 입력할 수 있습니다.', 'bulk-entry-message');
      return;
    }
    const existing = entriesByDate.get(visitDate);
    if (existing && existing.daily_visitors === dailyVisitors) {
      showUserError('변경된 내용이 없습니다.', 'bulk-entry-message');
      return;
    }
    setFormMessage('bulk-entry-message', '');
    try {
      pendingEntryMonth = new Date(visitDate);
      await apiRequest(`/visitors/years/${currentYear.id}/entries/bulk`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entries: [{ visit_date: visitDate, daily_visitors: dailyVisitors }]
        })
      });
      await loadYearDetail(currentYear.id);
      resetBulkEntryForm();
    } catch (error) {
      showUserError(error.message || '저장에 실패했습니다.', 'bulk-entry-message');
    }
  });

  getElement('bulk-month-load')?.addEventListener('click', () => {
    bulkMonthCursor = null;
    renderBulkMonthTable();
  });

  getElement('save-bulk-month')?.addEventListener('click', async () => {
    if (!currentYear) return;
    const inputs = Array.from(document.querySelectorAll('.bulk-month-input'));
    const parsed = [];
    for (const input of inputs) {
      const dateStr = input.dataset.date;
      const value = parseNumberInput(input.value);
      const original = parseNumberInput(input.dataset.original);
      if (value === null) continue;
      if (value < 0 || value > 1000000) {
        showUserError('금일 출입자는 0 이상 1,000,000 이하만 입력할 수 있습니다.', 'bulk-entry-message');
        return;
      }
      if (original === value) continue;
      parsed.push({ visitDate: dateStr, dailyVisitors: value });
    }
    if (!parsed.length) {
      showUserError('변경된 데이터가 없습니다.', 'bulk-entry-message');
      return;
    }
    parsed.sort((a, b) => new Date(a.visitDate) - new Date(b.visitDate));
    pendingEntryMonth = new Date(parsed[0].visitDate);
    setFormMessage('bulk-entry-message', '');
    try {
      const payload = {
        entries: parsed.map((item) => ({
          visit_date: item.visitDate,
          daily_visitors: item.dailyVisitors
        }))
      };
      await apiRequest(`/visitors/years/${currentYear.id}/entries/bulk`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      await loadYearDetail(currentYear.id);
      renderBulkMonthTable();
      alert(`총 ${parsed.length}건을 저장했습니다.`);
    } catch (error) {
      showUserError(error.message || '월간 저장에 실패했습니다.', 'bulk-entry-message');
    }
  });

  getElement('reset-month-entries')?.addEventListener('click', async () => {
    if (!currentYear) return;
    const monthValue = getElement('reset-month')?.value;
    if (!monthValue) {
      showUserError('삭제할 월을 선택하세요.', 'bulk-entry-message');
      return;
    }
    if (!confirm(`${monthValue} 월의 기록을 초기화할까요?`)) return;
    try {
      await apiRequest(`/visitors/years/${currentYear.id}/entries?month=${monthValue}`, {
        method: 'DELETE'
      });
      await loadYearDetail(currentYear.id);
      alert('월간 데이터가 초기화되었습니다.');
    } catch (error) {
      showUserError(error.message || '월간 초기화에 실패했습니다.', 'bulk-entry-message');
    }
  });

  getElement('delete-year')?.addEventListener('click', async () => {
    if (!currentYear) return;
    if (!confirm('학년도 삭제 시 데이터와 기간 설정이 모두 삭제됩니다. 계속할까요?')) return;
    try {
      await apiRequest(`/visitors/years/${currentYear.id}`, { method: 'DELETE' });
      alert('학년도 삭제가 완료되었습니다.');
      await loadYears();
    } catch (error) {
      showUserError(error.message || '학년도 삭제에 실패했습니다.', 'bulk-entry-message');
    }
  });

  getElement('reset-bulk-entry')?.addEventListener('click', resetBulkEntryForm);

  getElement('delete-bulk-entry')?.addEventListener('click', async () => {
    if (!currentYear || !selectedEntryId) return;
    if (!confirm('선택한 기록을 삭제할까요?')) return;
    try {
      await apiRequest(`/visitors/years/${currentYear.id}/entries/${selectedEntryId}`, {
        method: 'DELETE'
      });
      await loadYearDetail(currentYear.id);
      resetBulkEntryForm();
    } catch (error) {
      showUserError(error.message || '삭제에 실패했습니다.', 'bulk-entry-message');
    }
  });

  ['visit-date', 'count1', 'count2'].forEach((id) => {
    getElement(id)?.addEventListener('input', () => updateEntryPreview());
    getElement(id)?.addEventListener('change', () => updateEntryPreview());
  });
  getElement('prev-total')?.addEventListener('input', () => updateEntryPreview());
  getElement('prev-total')?.addEventListener('change', () => updateEntryPreview());
  getElement('load-prev-total')?.addEventListener('click', loadPreviousTotal);

  ['bulk-visit-date', 'bulk-daily-visitors'].forEach((id) => {
    getElement(id)?.addEventListener('input', () => updateBulkEntryPreview());
    getElement(id)?.addEventListener('change', () => updateBulkEntryPreview());
  });

  getElement('entry-month-prev')?.addEventListener('click', () => moveEntryMonth(-1));
  getElement('entry-month-next')?.addEventListener('click', () => moveEntryMonth(1));

  getElement('calendar-prev')?.addEventListener('click', () => moveCalendar(-1));
  getElement('calendar-next')?.addEventListener('click', () => moveCalendar(1));

  getElement('save-periods')?.addEventListener('click', async () => {
    if (!currentYear) return;
    const payload = [
      {
        period_type: 'SEMESTER_1',
        name: periodTypes.SEMESTER_1,
        start_date: getElement('period-semester1-start')?.value || null,
        end_date: getElement('period-semester1-end')?.value || null
      },
      {
        period_type: 'SUMMER_BREAK',
        name: periodTypes.SUMMER_BREAK,
        start_date: getElement('period-summer-start')?.value || null,
        end_date: getElement('period-summer-end')?.value || null
      },
      {
        period_type: 'SEMESTER_2',
        name: periodTypes.SEMESTER_2,
        start_date: getElement('period-semester2-start')?.value || null,
        end_date: getElement('period-semester2-end')?.value || null
      },
      {
        period_type: 'WINTER_BREAK',
        name: periodTypes.WINTER_BREAK,
        start_date: getElement('period-winter-start')?.value || null,
        end_date: getElement('period-winter-end')?.value || null
      }
    ];
    try {
      await apiRequest(`/visitors/years/${currentYear.id}/periods`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      await loadYearDetail(currentYear.id);
    } catch (error) {
      showUserError(error.message || '기간 저장에 실패했습니다.', 'entry-message');
    }
  });
}

export function initVisitorStats() {
  bindEvents();
  loadYears().catch((error) => {
    console.error(error);
    const status = getElement('entry-status');
    const message = error?.message || '데이터를 불러오지 못했습니다.';
    if (status) status.textContent = message;
    setFormMessage('entry-message', message);
    setFormMessage('bulk-entry-message', message);
    alert(message);
  });
}

import { apiRequest } from './api.js';
import { initAppLayout } from './layout.js';
import { formatDateTimeSeoul } from './datetime.js';

const backupList = document.getElementById('backup-list');
const backupMessage = document.getElementById('backup-message');
const exportMessage = document.getElementById('export-message');
const validationResult = document.getElementById('validation-result');
const restoreList = document.getElementById('restore-list');
const restoreHistoryCard = document.getElementById('restore-history-card');
const uploadFileInput = document.getElementById('upload-backup-file');
const uploadValidateButton = document.getElementById('upload-validate');
const uploadRestoreButton = document.getElementById('upload-restore');
const uploadMessage = document.getElementById('upload-message');
let currentUser = null;
const validationState = new Map();
let lastUploadValidation = null;
const visitorYearSelect = document.getElementById('visitor-year-select');
const visitorYearHint = document.getElementById('visitor-year-hint');
const exportVisitorsButton = document.getElementById('export-visitors');

function setMessage(target, text, isError = false) {
  if (!target) return;
  target.textContent = text;
  target.classList.toggle('error', isError);
}


function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}


function parseDateInput(value) {
  if (!value) return null;
  const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (match) return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function selectDefaultVisitorYear(years) {
  const today = new Date();
  const containingToday = years.find((year) => {
    const start = parseDateInput(year.start_date);
    const end = parseDateInput(year.end_date);
    return start && end && today >= start && today <= end;
  });
  return containingToday || [...years].sort((a, b) => (b.academic_year || 0) - (a.academic_year || 0))[0] || null;
}

async function loadVisitorYearOptions() {
  if (!visitorYearSelect) return;
  try {
    const years = await apiRequest('/visitors/years');
    visitorYearSelect.innerHTML = '';
    if (!years.length) {
      visitorYearSelect.disabled = true;
      if (exportVisitorsButton) exportVisitorsButton.disabled = true;
      if (visitorYearHint) visitorYearHint.textContent = '등록된 출입 학년도가 없습니다. 출입 통계 설정에서 학년도를 먼저 생성하세요.';
      return;
    }
    years.forEach((year) => {
      const option = document.createElement('option');
      option.value = year.id;
      option.textContent = year.label || `${year.academic_year}학년도`;
      visitorYearSelect.appendChild(option);
    });
    const selected = selectDefaultVisitorYear(years);
    if (selected) visitorYearSelect.value = selected.id;
    visitorYearSelect.disabled = false;
    if (exportVisitorsButton) exportVisitorsButton.disabled = false;
    if (visitorYearHint) visitorYearHint.textContent = '다운로드할 출입 학년도를 선택하세요.';
  } catch (e) {
    visitorYearSelect.disabled = true;
    if (exportVisitorsButton) exportVisitorsButton.disabled = true;
    if (visitorYearHint) visitorYearHint.textContent = e.message || '출입 학년도 목록을 불러오지 못했습니다.';
  }
}

function formatSize(size) {
  if (!size) return '-';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function filenameFromContentDisposition(response, fallback = 'download') {
  const disposition = response.headers.get('content-disposition') || '';
  const utf8Match = disposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1].replace(/^"|"$/g, ''));
  }

  const asciiMatch = disposition.match(/filename="?([^";]+)"?/i);
  if (asciiMatch?.[1]) {
    return decodeURIComponent(asciiMatch[1]);
  }

  return fallback;
}

async function downloadFile(path, fallbackFilename = 'download') {
  const resp = await apiRequest(path, { responseType: 'blob' });
  const blob = await resp.blob();
  const filename = filenameFromContentDisposition(resp, fallbackFilename);
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function renderBackups(backups) {
  if (!backupList) return;
  if (!backups.length) {
    backupList.innerHTML = '<tr><td colspan="6" class="muted">생성된 백업이 없습니다.</td></tr>';
    return;
  }
  backupList.innerHTML = '';
  backups.forEach((backup) => {
    const tr = document.createElement('tr');
    const createdAt = backup.created_at ? formatDateTimeSeoul(backup.created_at) : '-';
    const isMaster = currentUser?.role === 'MASTER';
    const validation = validationState.get(backup.id);
    const canManageBackup = backup.domain !== 'FULL' || isMaster;
    const canDownloadBackup = backup.domain === 'VISITORS' || backup.domain === 'SERIALS';
    const downloadButton = canDownloadBackup
      ? `<button class="btn tiny secondary" type="button" data-download="${backup.id}">다운로드</button>`
      : '';
    const validateButton = canManageBackup
      ? `<button class="btn tiny secondary" type="button" data-validate="${backup.id}">검증</button>`
      : '';
    const restoreButton = canManageBackup
      ? `<button class="btn tiny" type="button" data-restore="${backup.id}" ${validation?.valid ? '' : 'disabled'}>복원</button>`
      : '';
    const deleteButton = isMaster
      ? `<button class="btn tiny muted" type="button" data-delete="${backup.id}">목록에서 제외</button>`
      : '';
    tr.innerHTML = `
      <td>${backup.domain}</td>
      <td>${backup.file_name}</td>
      <td>${formatSize(backup.file_size)}</td>
      <td>${createdAt}</td>
      <td>${backup.status}</td>
      <td>
        ${downloadButton}
        ${validateButton}
        ${restoreButton}
        ${deleteButton}
      </td>
    `;
    backupList.appendChild(tr);
  });
}

function renderValidation(result) {
  const lines = [
    `검증 결과: ${result.valid ? '정상' : '실패'}`,
    result.domain ? `범위: ${result.domain}` : null,
    result.schema_version ? `스키마: ${result.schema_version}` : null,
    result.summary ? `데이터: ${Object.entries(result.summary).map(([key, value]) => `${key} ${value}`).join(', ')}` : null,
  ].filter(Boolean);
  if (result.errors?.length) lines.push(`오류: ${result.errors.join(' / ')}`);
  if (result.warnings?.length) lines.push(`경고: ${result.warnings.join(' / ')}`);
  setMessage(validationResult, lines.join('\n'), !result.valid);
}

function renderUploadValidation(result) {
  const lines = [
    `검증 결과: ${result.valid ? '정상' : '실패'}`,
    result.domain ? `범위: ${result.domain}` : null,
    result.schema_version ? `스키마: ${result.schema_version}` : null,
    result.summary ? `데이터: ${Object.entries(result.summary).map(([key, value]) => `${key} ${value}`).join(', ')}` : null,
  ].filter(Boolean);
  if (result.errors?.length) lines.push(`오류: ${result.errors.join(' / ')}`);
  if (result.warnings?.length) lines.push(`경고: ${result.warnings.join(' / ')}`);
  setMessage(uploadMessage, lines.join('\n'), !result.valid);
  if (uploadRestoreButton) {
    uploadRestoreButton.disabled = !(currentUser?.role !== 'MEMBER' && result.valid);
  }
}

function restoreJobIsAfterCutoff(job, cutoffAt) {
  if (!cutoffAt) return true;
  const cutoffTime = new Date(cutoffAt).getTime();
  if (Number.isNaN(cutoffTime)) return true;
  return [job.started_at, job.finished_at]
    .filter(Boolean)
    .some((value) => {
      const jobTime = new Date(value).getTime();
      return !Number.isNaN(jobTime) && jobTime >= cutoffTime;
    });
}

function ensureRestoreHistoryArchiveList() {
  const table = restoreList?.closest('table');
  const tableWrap = restoreList?.closest('.table-wrap');
  const section = tableWrap?.parentElement;
  if (!table || !tableWrap || !section) return null;
  let note = document.getElementById('restore-history-cutoff-note');
  if (!note) {
    note = document.createElement('div');
    note.id = 'restore-history-cutoff-note';
    note.className = 'form-message show';
    section.insertBefore(note, tableWrap);
  }
  let details = document.getElementById('restore-history-archive');
  if (!details) {
    details = document.createElement('details');
    details.id = 'restore-history-archive';
    const summary = document.createElement('summary');
    summary.id = 'restore-history-archive-summary';
    details.appendChild(summary);
    const archiveWrap = document.createElement('div');
    archiveWrap.className = 'table-wrap';
    const archiveTable = table.cloneNode(true);
    archiveTable.querySelector('tbody')?.setAttribute('id', 'restore-archive-list');
    archiveWrap.appendChild(archiveTable);
    details.appendChild(archiveWrap);
    section.insertBefore(details, tableWrap.nextSibling);
  }
  return {
    note,
    details,
    summary: document.getElementById('restore-history-archive-summary'),
    archiveList: document.getElementById('restore-archive-list'),
  };
}

function appendRestoreRows(target, jobs, { archived = false, emptyText = '복원 이력이 없습니다.' } = {}) {
  if (!target) return;
  if (!jobs.length) {
    target.innerHTML = `<tr><td colspan="5" class="muted">${escapeHtml(emptyText)}</td></tr>`;
    return;
  }
  target.innerHTML = '';
  jobs.forEach((job) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${escapeHtml(job.domain)}${archived ? '<div><span class="badge pending">초기화 이전</span></div>' : ''}</td>
      <td>${escapeHtml(job.mode)}</td>
      <td>${escapeHtml(job.status)}</td>
      <td>${job.started_at ? formatDateTimeSeoul(job.started_at) : '-'}</td>
      <td>${job.finished_at ? formatDateTimeSeoul(job.finished_at) : '-'}</td>
    `;
    target.appendChild(tr);
  });
}

function renderRestoreJobs(jobs, cutoff = null) {
  if (!restoreList) return;
  const controls = ensureRestoreHistoryArchiveList();
  const cutoffAt = cutoff?.latest_reset_at || null;
  const visibleJobs = cutoffAt ? jobs.filter((job) => restoreJobIsAfterCutoff(job, cutoffAt)) : jobs;
  const archivedJobs = cutoffAt ? jobs.filter((job) => !restoreJobIsAfterCutoff(job, cutoffAt)) : [];
  if (controls?.note) {
    controls.note.innerHTML = cutoffAt
      ? `기본 표시: 최근 전체 초기화 이후 복원 이력<br>초기화 기준: ${escapeHtml(formatDateTimeSeoul(cutoffAt))} · 시스템 전체 초기화`
      : '기본 표시: 전체 복원 이력<br>아직 초기화 기준점이 없습니다.';
  }
  appendRestoreRows(restoreList, visibleJobs, { emptyText: cutoffAt ? '최근 전체 초기화 이후 복원 이력이 없습니다.' : '복원 이력이 없습니다.' });
  if (controls?.details && controls?.summary && controls?.archiveList) {
    controls.details.style.display = cutoffAt ? '' : 'none';
    controls.summary.textContent = `초기화 이전 복원 이력 ${archivedJobs.length}건 보기`;
    appendRestoreRows(controls.archiveList, archivedJobs, { archived: true, emptyText: '초기화 이전 복원 이력이 없습니다.' });
  }
}

async function loadBackups() {
  const backups = await apiRequest('/data/backups');
  renderBackups(backups);
}

async function loadRestoreJobs() {
  if (currentUser?.role !== 'MASTER') return;
  if (restoreHistoryCard) restoreHistoryCard.style.display = '';
  const [jobs, cutoff] = await Promise.all([
    apiRequest('/data/restores'),
    apiRequest('/data/restores/reset-cutoff?domain=FULL'),
  ]);
  renderRestoreJobs(jobs, cutoff);
}

async function createBackup() {
  const button = document.getElementById('create-backup');
  const domain = document.getElementById('backup-domain')?.value || 'VISITORS';
  const description = document.getElementById('backup-description')?.value.trim() || null;
  button.disabled = true;
  setMessage(backupMessage, '백업 생성 중...');
  try {
    await apiRequest('/data/backups', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ domain, description }),
    });
    setMessage(backupMessage, '백업이 생성되었습니다.');
    await loadBackups();
  } catch (e) {
    setMessage(backupMessage, e.message || '백업 생성에 실패했습니다.', true);
  } finally {
    button.disabled = false;
  }
}

async function exportVisitors() {
  const yearId = visitorYearSelect?.value;
  if (!yearId) {
    setMessage(exportMessage, '다운로드할 학년도를 선택하세요.', true);
    return;
  }
  const selectedOption = visitorYearSelect?.options?.[visitorYearSelect.selectedIndex];
  const yearText = selectedOption?.textContent?.match(/\d{4}/)?.[0] || 'visitors';
  const fallbackFilename = `${yearText}학년도 참고열람실 출입자 통계.xlsx`;
  setMessage(exportMessage, '방문자 Excel 생성 중...');
  try {
    await downloadFile(`/data/exports/visitors/excel?year_id=${encodeURIComponent(yearId)}`, fallbackFilename);
    setMessage(exportMessage, '방문자 Excel 다운로드를 시작했습니다.');
  } catch (e) {
    setMessage(exportMessage, e.message || 'Excel 다운로드에 실패했습니다.', true);
  }
}

async function exportSerials() {
  setMessage(exportMessage, '연속간행물 Excel 생성 중...');
  try {
    await downloadFile('/data/exports/serials/excel');
    setMessage(exportMessage, '연속간행물 Excel 다운로드를 시작했습니다.');
  } catch (e) {
    setMessage(exportMessage, e.message || 'Excel 다운로드에 실패했습니다.', true);
  }
}

currentUser = await initAppLayout('data-management');
if (currentUser?.role !== 'MEMBER' && uploadRestoreButton) {
  uploadRestoreButton.style.display = '';
}
if (currentUser?.role === 'MASTER') {
  document.querySelectorAll('#backup-domain [data-master-only]').forEach((option) => {
    option.hidden = false;
  });
}
if (currentUser?.role !== 'MEMBER') {
  document.querySelectorAll('#backup-domain [data-operator-only]').forEach((option) => {
    option.hidden = false;
  });
}

document.getElementById('create-backup')?.addEventListener('click', createBackup);
document.getElementById('export-visitors')?.addEventListener('click', exportVisitors);
document.getElementById('export-serials')?.addEventListener('click', exportSerials);
uploadFileInput?.addEventListener('change', () => {
  lastUploadValidation = null;
  if (uploadRestoreButton) uploadRestoreButton.disabled = true;
  setMessage(uploadMessage, '');
});
uploadValidateButton?.addEventListener('click', async () => {
  const file = uploadFileInput?.files?.[0];
  if (!file) {
    setMessage(uploadMessage, 'JSON 백업 파일을 선택하세요.', true);
    return;
  }
  uploadValidateButton.disabled = true;
  setMessage(uploadMessage, '업로드 파일 검증 중...');
  try {
    const form = new FormData();
    form.append('file', file);
    const result = await apiRequest('/data/backups/upload/validate', {
      method: 'POST',
      body: form,
    });
    lastUploadValidation = result;
    renderUploadValidation(result);
  } catch (e) {
    lastUploadValidation = null;
    if (uploadRestoreButton) uploadRestoreButton.disabled = true;
    setMessage(uploadMessage, e.message || '업로드 검증에 실패했습니다.', true);
  } finally {
    uploadValidateButton.disabled = false;
  }
});
uploadRestoreButton?.addEventListener('click', async () => {
  const file = uploadFileInput?.files?.[0];
  if (!file) {
    setMessage(uploadMessage, 'JSON 백업 파일을 선택하세요.', true);
    return;
  }
  if (!lastUploadValidation?.valid) {
    setMessage(uploadMessage, '검증 성공 후 복원할 수 있습니다.', true);
    return;
  }
  const confirmText = window.prompt('복원을 진행하려면 "복원합니다"를 입력하세요.');
  if (confirmText !== '복원합니다') {
    setMessage(uploadMessage, '복원 확인 문구가 일치하지 않습니다.', true);
    return;
  }
  uploadRestoreButton.disabled = true;
  setMessage(uploadMessage, '업로드 복원 진행 중...');
  try {
    const form = new FormData();
    form.append('file', file);
    form.append('confirm_text', confirmText);
    const job = await apiRequest('/data/backups/upload/restore', {
      method: 'POST',
      body: form,
    });
    setMessage(uploadMessage, `업로드 복원 완료: ${job.status}`);
    await loadBackups();
    await loadRestoreJobs();
  } catch (e) {
    setMessage(uploadMessage, e.message || '업로드 복원에 실패했습니다.', true);
  } finally {
    uploadRestoreButton.disabled = !(currentUser?.role !== 'MEMBER' && lastUploadValidation?.valid);
  }
});
backupList?.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-download], [data-sanitized-download], [data-validate], [data-restore], [data-delete]');
  if (!button) return;
  button.disabled = true;
  try {
    if (button.dataset.download) {
      setMessage(backupMessage, '백업 다운로드 중...');
      await downloadFile(`/data/backups/${button.dataset.download}/download`);
      setMessage(backupMessage, '백업 다운로드를 시작했습니다.');
    } else if (button.dataset.sanitizedDownload) {
      setMessage(backupMessage, '민감정보 제외 백업 다운로드 중...');
      await downloadFile(`/data/backups/${button.dataset.sanitizedDownload}/download?sanitize=true`);
      setMessage(backupMessage, '민감정보 제외 다운로드를 시작했습니다.');
    } else if (button.dataset.validate) {
      setMessage(validationResult, '백업 검증 중...');
      const result = await apiRequest(`/data/backups/${button.dataset.validate}/validate`, { method: 'POST' });
      validationState.set(button.dataset.validate, result);
      renderValidation(result);
      await loadBackups();
    } else if (button.dataset.restore) {
      const confirmText = window.prompt('복원을 진행하려면 "복원합니다"를 입력하세요.');
      if (confirmText !== '복원합니다') {
        setMessage(validationResult, '복원 확인 문구가 일치하지 않습니다.', true);
        return;
      }
      setMessage(validationResult, '복원 진행 중...');
      const job = await apiRequest(`/data/backups/${button.dataset.restore}/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: 'REPLACE', confirm_text: confirmText }),
      });
      setMessage(validationResult, `복원 완료: ${job.status}`);
      await loadBackups();
      await loadRestoreJobs();
    } else if (button.dataset.delete) {
      if (!window.confirm('이 백업을 백업 목록에서 제외합니다. 실제 저장소 파일은 삭제되지 않습니다.')) {
        return;
      }
      setMessage(backupMessage, '백업을 목록에서 제외하는 중...');
      await apiRequest(`/data/backups/${button.dataset.delete}`, { method: 'DELETE' });
      setMessage(backupMessage, '백업 목록에서 제외했습니다. 저장소 파일은 유지됩니다.');
      validationState.delete(button.dataset.delete);
      await loadBackups();
    }
  } catch (e) {
    setMessage(validationResult, e.message || '요청 처리에 실패했습니다.', true);
  } finally {
    button.disabled = false;
  }
});

await loadVisitorYearOptions();

try {
  await loadBackups();
  await loadRestoreJobs();
} catch (e) {
  if (backupList) backupList.innerHTML = '<tr><td colspan="6" class="error">백업 목록을 불러오지 못했습니다.</td></tr>';
}

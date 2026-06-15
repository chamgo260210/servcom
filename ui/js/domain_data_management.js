import { apiRequest } from './api.js';
import { initAppLayout } from './layout.js';

const CONFIGS = {
  WORK: {
    activePage: 'work-data-management',
    title: '근무 데이터',
    canDownload: false,
    canUpload: false,
    canExcel: false,
    masterOnly: false,
    restoreWarning: '근무 배정, 근무 변경 요청, 관련 이력이 백업 시점으로 교체됩니다.',
  },
  VISITORS: {
    activePage: 'visitors-data-management',
    title: '출입 통계 데이터',
    canDownload: true,
    canUpload: true,
    canExcel: true,
    excelPath: '/data/exports/visitors/excel',
    masterOnly: false,
    restoreWarning: '출입 통계 데이터가 백업 시점으로 교체됩니다.',
  },
  SERIALS: {
    activePage: 'serials-data-management',
    title: '연속간행물 데이터',
    canDownload: true,
    canUpload: true,
    canExcel: true,
    excelPath: '/data/exports/serials/excel',
    masterOnly: false,
    restoreWarning: '연속간행물 배치와 간행물 데이터가 백업 시점으로 교체됩니다.',
  },
  FULL: {
    activePage: 'system-backup-management',
    title: '시스템 백업',
    canDownload: false,
    canUpload: false,
    canExcel: false,
    masterOnly: true,
    restoreWarning: '전체 시스템 데이터가 교체됩니다. 계정과 권한 정보가 바뀌어 복원 후 재로그인이 필요할 수 있습니다.',
  },
};

const domain = (document.body.dataset.domain || '').toUpperCase();
const config = CONFIGS[domain] || CONFIGS.VISITORS;
const roleOrder = { MEMBER: 1, OPERATOR: 2, MASTER: 3 };
const validationState = new Map();
let currentUser = null;
let lastUploadValidation = null;

const backupList = document.getElementById('backup-list');
const restoreList = document.getElementById('restore-list');
const backupMessage = document.getElementById('backup-message');
const validationResult = document.getElementById('validation-result');
const uploadSection = document.getElementById('upload-section');
const uploadFileInput = document.getElementById('upload-backup-file');
const uploadValidateButton = document.getElementById('upload-validate');
const uploadRestoreButton = document.getElementById('upload-restore');
const uploadMessage = document.getElementById('upload-message');
const excelSection = document.getElementById('excel-section');
const excelButton = document.getElementById('export-excel');
const excelMessage = document.getElementById('excel-message');
const yearInput = document.getElementById('visitor-academic-year');
const domainTitle = document.getElementById('domain-title');
const domainSubtitle = document.getElementById('domain-subtitle');

function setMessage(target, text, isError = false) {
  if (!target) return;
  target.textContent = text || '';
  target.classList.toggle('error', Boolean(isError));
}

function formatSize(size) {
  if (!size) return '-';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function isAllowed() {
  if (!currentUser) return false;
  if (config.masterOnly) return currentUser.role === 'MASTER';
  return roleOrder[currentUser.role] >= roleOrder.OPERATOR;
}

async function downloadFile(path) {
  const resp = await apiRequest(path, { responseType: 'blob' });
  const blob = await resp.blob();
  const disposition = resp.headers.get('content-disposition') || '';
  const match = disposition.match(/filename="?([^"]+)"?/i);
  const filename = match?.[1] || 'download';
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = decodeURIComponent(filename);
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function renderValidation(result, target = validationResult) {
  const lines = [
    `검증 결과: ${result.valid ? '정상' : '실패'}`,
    result.domain ? `도메인: ${result.domain}` : null,
    result.schema_version ? `스키마: ${result.schema_version}` : null,
    result.summary ? `데이터: ${Object.entries(result.summary).map(([key, value]) => `${key} ${value}`).join(', ')}` : null,
  ].filter(Boolean);
  if (result.errors?.length) lines.push(`오류: ${result.errors.join(' / ')}`);
  if (result.warnings?.length) lines.push(`경고: ${result.warnings.join(' / ')}`);
  setMessage(target, lines.join('\n'), !result.valid);
}

function restorePointLabel(job) {
  const summary = job.summary || {};
  return summary.restore_point_backup_id || summary.pre_restore_backup_id || '-';
}

function renderBackups(backups) {
  if (!backupList) return;
  if (!backups.length) {
    backupList.innerHTML = '<tr><td colspan="6" class="muted">생성된 백업이 없습니다.</td></tr>';
    return;
  }
  backupList.innerHTML = '';
  backups.forEach((backup) => {
    const validation = validationState.get(backup.id);
    const tr = document.createElement('tr');
    const downloadButton = config.canDownload
      ? `<button class="btn tiny secondary" type="button" data-download="${backup.id}">다운로드</button>`
      : '';
    const restoreButton = `<button class="btn tiny" type="button" data-restore="${backup.id}" ${validation?.valid ? '' : 'disabled'}>복원</button>`;
    tr.innerHTML = `
      <td>${backup.file_name}</td>
      <td>${formatSize(backup.file_size)}</td>
      <td>${backup.created_at ? new Date(backup.created_at).toLocaleString('ko-KR') : '-'}</td>
      <td>${backup.status}</td>
      <td>${backup.description || '-'}</td>
      <td>
        ${downloadButton}
        <button class="btn tiny secondary" type="button" data-validate="${backup.id}">검증</button>
        ${restoreButton}
      </td>
    `;
    backupList.appendChild(tr);
  });
}

function renderRestoreJobs(jobs) {
  if (!restoreList) return;
  if (!jobs.length) {
    restoreList.innerHTML = '<tr><td colspan="6" class="muted">복원 이력이 없습니다.</td></tr>';
    return;
  }
  restoreList.innerHTML = '';
  jobs.forEach((job) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${job.mode}</td>
      <td>${job.status}</td>
      <td>${job.started_at ? new Date(job.started_at).toLocaleString('ko-KR') : '-'}</td>
      <td>${job.finished_at ? new Date(job.finished_at).toLocaleString('ko-KR') : '-'}</td>
      <td>${restorePointLabel(job)}</td>
      <td>${job.error_message || '-'}</td>
    `;
    restoreList.appendChild(tr);
  });
}

async function loadBackups() {
  const backups = await apiRequest(`/data/backups?domain=${encodeURIComponent(domain)}`);
  renderBackups(backups);
}

async function loadRestoreJobs() {
  const jobs = await apiRequest(`/data/restores?domain=${encodeURIComponent(domain)}`);
  renderRestoreJobs(jobs);
}

async function createBackup() {
  const button = document.getElementById('create-backup');
  const description = document.getElementById('backup-description')?.value.trim() || null;
  if (!button) return;
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

async function exportExcel() {
  if (!config.canExcel || !config.excelPath) return;
  let path = config.excelPath;
  if (domain === 'VISITORS') {
    const academicYear = yearInput?.value;
    if (!academicYear) {
      setMessage(excelMessage, '학년도를 입력하세요.', true);
      return;
    }
    path = `${path}?academic_year=${encodeURIComponent(academicYear)}`;
  }
  setMessage(excelMessage, 'Excel 생성 중...');
  try {
    await downloadFile(path);
    setMessage(excelMessage, 'Excel 다운로드를 시작했습니다.');
  } catch (e) {
    setMessage(excelMessage, e.message || 'Excel 다운로드에 실패했습니다.', true);
  }
}

async function validateUpload() {
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
    const result = await apiRequest('/data/backups/upload/validate', { method: 'POST', body: form });
    lastUploadValidation = result;
    renderValidation(result, uploadMessage);
    uploadRestoreButton.disabled = !(result.valid && result.domain === domain);
    if (result.valid && result.domain !== domain) {
      setMessage(uploadMessage, `${config.title} 백업 파일만 복원할 수 있습니다.`, true);
    }
  } catch (e) {
    lastUploadValidation = null;
    uploadRestoreButton.disabled = true;
    setMessage(uploadMessage, e.message || '업로드 검증에 실패했습니다.', true);
  } finally {
    uploadValidateButton.disabled = false;
  }
}

async function restoreUpload() {
  const file = uploadFileInput?.files?.[0];
  if (!file || !lastUploadValidation?.valid || lastUploadValidation.domain !== domain) {
    setMessage(uploadMessage, '현재 화면의 백업 파일을 검증한 뒤 복원할 수 있습니다.', true);
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
    const job = await apiRequest('/data/backups/upload/restore', { method: 'POST', body: form });
    setMessage(uploadMessage, `업로드 복원 완료: ${job.status}`);
    await loadBackups();
    await loadRestoreJobs();
  } catch (e) {
    setMessage(uploadMessage, e.message || '업로드 복원에 실패했습니다.', true);
  } finally {
    uploadRestoreButton.disabled = !(lastUploadValidation?.valid && lastUploadValidation.domain === domain);
  }
}

backupList?.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-download], [data-validate], [data-restore]');
  if (!button) return;
  button.disabled = true;
  try {
    if (button.dataset.download) {
      setMessage(backupMessage, '백업 다운로드 중...');
      await downloadFile(`/data/backups/${button.dataset.download}/download`);
      setMessage(backupMessage, '백업 다운로드를 시작했습니다.');
    } else if (button.dataset.validate) {
      setMessage(validationResult, '백업 검증 중...');
      const result = await apiRequest(`/data/backups/${button.dataset.validate}/validate`, { method: 'POST' });
      validationState.set(button.dataset.validate, result);
      renderValidation(result);
      await loadBackups();
    } else if (button.dataset.restore) {
      const warning = `${config.restoreWarning}\n\n복원을 진행하려면 "복원합니다"를 입력하세요.`;
      const confirmText = window.prompt(warning);
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
    }
  } catch (e) {
    setMessage(validationResult, e.message || '요청 처리에 실패했습니다.', true);
  } finally {
    button.disabled = false;
  }
});

uploadFileInput?.addEventListener('change', () => {
  lastUploadValidation = null;
  if (uploadRestoreButton) uploadRestoreButton.disabled = true;
  setMessage(uploadMessage, '');
});
uploadValidateButton?.addEventListener('click', validateUpload);
uploadRestoreButton?.addEventListener('click', restoreUpload);
excelButton?.addEventListener('click', exportExcel);
document.getElementById('create-backup')?.addEventListener('click', createBackup);

currentUser = await initAppLayout(config.activePage);
if (!isAllowed()) {
  alert('이 페이지에 접근할 권한이 없습니다.');
  window.location.href = document.body.dataset.home || 'html/dashboard.html';
  throw new Error('Access denied');
}
if (domainTitle) domainTitle.textContent = config.title;
if (domainSubtitle) domainSubtitle.textContent = config.restoreWarning;
if (uploadSection) uploadSection.style.display = config.canUpload ? '' : 'none';
if (excelSection) excelSection.style.display = config.canExcel ? '' : 'none';
if (yearInput) yearInput.closest('.form-row')?.classList.toggle('hidden', domain !== 'VISITORS');

try {
  await loadBackups();
  await loadRestoreJobs();
} catch (e) {
  if (backupList) backupList.innerHTML = '<tr><td colspan="6" class="error">백업 목록을 불러오지 못했습니다.</td></tr>';
  if (restoreList) restoreList.innerHTML = '<tr><td colspan="6" class="error">복원 이력을 불러오지 못했습니다.</td></tr>';
}

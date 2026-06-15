import { apiRequest } from './api.js';
import { initAppLayout } from './layout.js';

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

function setMessage(target, text, isError = false) {
  if (!target) return;
  target.textContent = text;
  target.classList.toggle('error', isError);
}

function formatSize(size) {
  if (!size) return '-';
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
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

function renderBackups(backups) {
  if (!backupList) return;
  if (!backups.length) {
    backupList.innerHTML = '<tr><td colspan="6" class="muted">생성된 백업이 없습니다.</td></tr>';
    return;
  }
  backupList.innerHTML = '';
  backups.forEach((backup) => {
    const tr = document.createElement('tr');
    const createdAt = backup.created_at ? new Date(backup.created_at).toLocaleString('ko-KR') : '-';
    const isMaster = currentUser?.role === 'MASTER';
    const validation = validationState.get(backup.id);
    const restoreButton = isMaster
      ? `<button class="btn tiny" type="button" data-restore="${backup.id}" ${validation?.valid ? '' : 'disabled'}>복원</button>`
      : '';
    tr.innerHTML = `
      <td>${backup.domain}</td>
      <td>${backup.file_name}</td>
      <td>${formatSize(backup.file_size)}</td>
      <td>${createdAt}</td>
      <td>${backup.status}</td>
      <td>
        <button class="btn tiny secondary" type="button" data-download="${backup.id}">다운로드</button>
        <button class="btn tiny secondary" type="button" data-validate="${backup.id}">검증</button>
        ${restoreButton}
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
    uploadRestoreButton.disabled = !(currentUser?.role === 'MASTER' && result.valid);
  }
}

function renderRestoreJobs(jobs) {
  if (!restoreList) return;
  if (!jobs.length) {
    restoreList.innerHTML = '<tr><td colspan="5" class="muted">복원 이력이 없습니다.</td></tr>';
    return;
  }
  restoreList.innerHTML = '';
  jobs.forEach((job) => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
      <td>${job.domain}</td>
      <td>${job.mode}</td>
      <td>${job.status}</td>
      <td>${job.started_at ? new Date(job.started_at).toLocaleString('ko-KR') : '-'}</td>
      <td>${job.finished_at ? new Date(job.finished_at).toLocaleString('ko-KR') : '-'}</td>
    `;
    restoreList.appendChild(tr);
  });
}

async function loadBackups() {
  const backups = await apiRequest('/data/backups');
  renderBackups(backups);
}

async function loadRestoreJobs() {
  if (currentUser?.role !== 'MASTER') return;
  if (restoreHistoryCard) restoreHistoryCard.style.display = '';
  const jobs = await apiRequest('/data/restores');
  renderRestoreJobs(jobs);
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
  const academicYear = document.getElementById('visitor-academic-year')?.value;
  if (!academicYear) {
    setMessage(exportMessage, '방문자 학년도를 입력하세요.', true);
    return;
  }
  setMessage(exportMessage, '방문자 Excel 생성 중...');
  try {
    await downloadFile(`/data/exports/visitors/excel?academic_year=${encodeURIComponent(academicYear)}`);
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
if (currentUser?.role === 'MASTER' && uploadRestoreButton) {
  uploadRestoreButton.style.display = '';
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
    uploadRestoreButton.disabled = !(currentUser?.role === 'MASTER' && lastUploadValidation?.valid);
  }
});
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
    }
  } catch (e) {
    setMessage(validationResult, e.message || '요청 처리에 실패했습니다.', true);
  } finally {
    button.disabled = false;
  }
});

try {
  await loadBackups();
  await loadRestoreJobs();
} catch (e) {
  if (backupList) backupList.innerHTML = '<tr><td colspan="6" class="error">백업 목록을 불러오지 못했습니다.</td></tr>';
}

import { apiRequest } from './api.js';
import { initAppLayout } from './layout.js';
import { formatDateTimeSeoul } from './datetime.js';

const CONFIGS = {
  WORK: {
    activePage: 'work-data-management',
    title: '근무 데이터',
    createLabel: 'WORK 백업 생성',
    canDownload: false,
    canUpload: false,
    canExcel: false,
    restorable: true,
    masterOnly: false,
    restoreWarning: 'WORK 복원은 구성원 계정, 근무 배정, 변경 신청의 현재 상태를 복원합니다. 과거 활동 로그는 복원 대상이 아니며, 복원된 신청은 현재 상태 기준으로 피드에 보강 표시됩니다. 백업에 없는 기존 MEMBER는 비활성화됩니다.',
  },
  WORK_SYSTEM: {
    activePage: 'work-system-backup-management',
    title: '근무 시스템 백업',
    createLabel: '근무 시스템 백업 생성',
    canDownload: false,
    canUpload: false,
    canExcel: false,
    restorable: true,
    masterOnly: true,
    restoreWarning: '근무 시스템 복원은 OPERATOR/MEMBER 계정, 근무표, 변경 신청, 근무 관련 이력을 백업 시점 기준으로 복원합니다. MASTER 계정과 다른 도메인 데이터는 유지됩니다.',
  },
  VISITORS: {
    activePage: 'visitors-data-management',
    title: '출입 통계 데이터',
    createLabel: 'VISITORS 백업 생성',
    canDownload: true,
    canUpload: true,
    canExcel: true,
    restorable: true,
    excelPath: '/data/exports/visitors/excel',
    masterOnly: false,
    restoreWarning: '출입 통계 데이터가 백업 시점으로 교체됩니다.',
  },
  SERIALS: {
    activePage: 'serials-data-management',
    title: '연속간행물 데이터',
    createLabel: 'SERIALS 백업 생성',
    canDownload: true,
    canUpload: true,
    canExcel: true,
    restorable: true,
    excelPath: '/data/exports/serials/excel',
    masterOnly: false,
    restoreWarning: '연속간행물 배치와 간행물 데이터가 백업 시점으로 교체됩니다.',
  },
  FULL: {
    activePage: 'system-backup-management',
    title: '시스템 백업',
    createLabel: 'FULL 백업 생성',
    canDownload: false,
    canUpload: false,
    canExcel: false,
    restorable: true,
    masterOnly: true,
    restoreWarning: 'FULL 백업은 시스템 전체 데이터와 변동 이력(audit_logs)을 포함합니다. 복원 시 기존 이력은 삭제하지 않고 백업 이력을 병합합니다. 계정과 권한 정보가 바뀌어 복원 후 재로그인이 필요할 수 있습니다.',
  },
};

const domain = (document.body.dataset.domain || '').toUpperCase();
const config = CONFIGS[domain] || CONFIGS.VISITORS;
const roleOrder = { MEMBER: 1, OPERATOR: 2, MASTER: 3 };
const validationState = new Map();
let currentUser = null;
let lastUploadValidation = null;
let currentPreviewBackupId = null;
let showRestorePointFiles = true;
let lastStorageItems = [];

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
const storageBackupList = document.getElementById('storage-backup-list');
const storageMessage = document.getElementById('storage-message');
const storageValidationState = new Map();

function setMessage(target, text, isError = false) {
  if (!target) return;
  target.textContent = text || '';
  target.classList.toggle('error', Boolean(isError));
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
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

function restorePointHtml(job) {
  const summary = job.summary || {};
  const info = summary.restore_point || {};
  if (info.file_name) {
    const status = info.status || '-';
    const createdAt = info.created_at ? formatDateTimeSeoul(info.created_at) : '-';
    const fileState = info.file_exists ? '파일 있음' : '파일 없음';
    return `
      <div>${escapeHtml(info.file_name)}</div>
      <div class="muted small">${escapeHtml(createdAt)} · ${escapeHtml(status)} · ${escapeHtml(fileState)}</div>
    `;
  }
  const fallback = restorePointLabel(job);
  if (fallback === '-') return '-';
  const status = info.status || 'UNKNOWN';
  return `
    <div class="muted small">${escapeHtml(fallback)}</div>
    <div class="muted small">${escapeHtml(status)}</div>
  `;
}

function canRollbackJob(job) {
  const summary = job.summary || {};
  return job.status === 'SUCCESS' && summary.rollback_available === true && !summary.rollback_used;
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
    const restoreButton = config.restorable === false
      ? `<button class="btn tiny muted" type="button" disabled>${domain === 'WORK_SYSTEM' ? '복원 미지원' : '복원'}</button>`
      : `<button class="btn tiny" type="button" data-restore="${backup.id}" ${validation?.valid ? '' : 'disabled'}>복원</button>`;
    const excludeButton = currentUser?.role === 'MASTER'
      ? `<button class="btn tiny muted" type="button" data-exclude="${backup.id}">목록에서 제외</button>`
      : '';
    tr.innerHTML = `
      <td>${escapeHtml(backup.file_name)}</td>
      <td>${formatSize(backup.file_size)}</td>
      <td>${backup.created_at ? formatDateTimeSeoul(backup.created_at) : '-'}</td>
      <td>${escapeHtml(backup.status)}</td>
      <td>${escapeHtml(backup.description || '-')}</td>
      <td>
        <button class="btn tiny secondary" type="button" data-preview="${backup.id}">미리보기</button>
        ${downloadButton}
        <button class="btn tiny secondary" type="button" data-validate="${backup.id}">검증</button>
        ${restoreButton}
        ${excludeButton}
      </td>
    `;
    backupList.appendChild(tr);
  });
}

function sampleValueHtml(value) {
  if (value === null || value === undefined) return '-';
  if (typeof value === 'object') return escapeHtml(JSON.stringify(value));
  return escapeHtml(value);
}

function sampleRowsHtml(rows) {
  if (!rows?.length) return '<p class="muted">표시할 샘플이 없습니다.</p>';
  const columns = Array.from(rows.reduce((set, row) => {
    if (row && typeof row === 'object' && !Array.isArray(row)) {
      Object.keys(row).forEach((key) => set.add(key));
    }
    return set;
  }, new Set()));
  if (!columns.length) return '<p class="muted">표시할 샘플이 없습니다.</p>';
  const head = columns.map((column) => `<th>${escapeHtml(column)}</th>`).join('');
  const body = rows.map((row) => `
    <tr>${columns.map((column) => `<td>${sampleValueHtml(row?.[column])}</td>`).join('')}</tr>
  `).join('');
  return `
    <div class="table-wrap preview-sample-table">
      <table class="data-table table-compact">
        <thead><tr>${head}</tr></thead>
        <tbody>${body}</tbody>
      </table>
    </div>
  `;
}

function ensurePreviewModal() {
  let modal = document.getElementById('backup-preview-modal');
  if (modal) return modal;
  modal = document.createElement('div');
  modal.id = 'backup-preview-modal';
  modal.className = 'modal-backdrop';
  modal.style.display = 'none';
  modal.innerHTML = `
    <div class="modal wide">
      <div class="modal-header">
        <h3>백업 미리보기</h3>
      </div>
      <div class="modal-body stack">
        <div id="backup-preview-message" class="form-message" role="alert" aria-live="polite"></div>
        <div id="backup-preview-content" class="stack preview-content"></div>
      </div>
      <div class="modal-footer">
        <button class="btn secondary" id="backup-preview-sensitive" type="button">민감정보 포함 보기</button>
        <button class="btn muted" id="backup-preview-close" type="button">닫기</button>
      </div>
    </div>
  `;
  document.body.appendChild(modal);
  modal.querySelector('#backup-preview-close')?.addEventListener('click', () => {
    modal.style.display = 'none';
  });
  modal.addEventListener('click', (event) => {
    if (event.target === modal) modal.style.display = 'none';
  });
  modal.querySelector('#backup-preview-sensitive')?.addEventListener('click', async () => {
    if (!currentPreviewBackupId) return;
    const confirmed = window.confirm('민감정보 포함 미리보기 요청은 감사 로그에 기록됩니다. 비밀번호, 토큰, secret 계열 필드는 관리자라도 원문 표시하지 않습니다. 계속하시겠습니까?');
    if (!confirmed) return;
    await openPreview(currentPreviewBackupId, true);
  });
  return modal;
}

function renderPreview(preview) {
  const content = document.getElementById('backup-preview-content');
  if (!content) return;
  const summaryRows = Object.entries(preview.summary || {})
    .map(([table, count]) => `<tr><td>${escapeHtml(table)}</td><td>${escapeHtml(count)}</td></tr>`)
    .join('') || '<tr><td colspan="2" class="muted">요약 데이터가 없습니다.</td></tr>';
  const sampleSections = Object.entries(preview.samples || {})
    .map(([table, rows]) => `
      <details class="preview-table-section" open>
        <summary>${escapeHtml(table)} <span class="muted small">${escapeHtml(rows.length)}건 샘플</span></summary>
        ${sampleRowsHtml(rows)}
      </details>
    `)
    .join('') || '<p class="muted">샘플 데이터가 없습니다.</p>';
  const warningText = (preview.warnings || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  const errorText = (preview.errors || []).map((item) => `<li>${escapeHtml(item)}</li>`).join('');
  content.innerHTML = `
    <section class="stack">
      <h4>기본 정보</h4>
      <div class="table-wrap">
        <table class="data-table">
          <tbody>
            <tr><th>도메인</th><td>${escapeHtml(preview.domain)}</td></tr>
            <tr><th>종류</th><td>${escapeHtml(preview.kind || '-')}</td></tr>
            <tr><th>스키마</th><td>${escapeHtml(preview.schema_version || '-')}</td></tr>
            <tr><th>생성일</th><td>${preview.created_at ? escapeHtml(formatDateTimeSeoul(preview.created_at)) : '-'}</td></tr>
            <tr><th>파일 크기</th><td>${formatSize(preview.file_size)}</td></tr>
            <tr><th>체크섬</th><td>${escapeHtml(preview.checksum || '-')}</td></tr>
            <tr><th>마스킹</th><td>${preview.masked ? '적용' : '미적용'}</td></tr>
          </tbody>
        </table>
      </div>
    </section>
    <section class="stack">
      <h4>테이블별 건수</h4>
      <div class="table-wrap"><table class="data-table"><thead><tr><th>테이블</th><th>건수</th></tr></thead><tbody>${summaryRows}</tbody></table></div>
    </section>
    <section class="stack">
      <h4>샘플 데이터</h4>
      ${sampleSections}
    </section>
    ${warningText ? `<section><h4>경고</h4><ul>${warningText}</ul></section>` : ''}
    ${errorText ? `<section><h4>검증 오류</h4><ul>${errorText}</ul></section>` : ''}
  `;
}

async function openPreview(backupId, sensitive = false) {
  currentPreviewBackupId = backupId;
  const modal = ensurePreviewModal();
  const sensitiveButton = modal.querySelector('#backup-preview-sensitive');
  const message = modal.querySelector('#backup-preview-message');
  const content = modal.querySelector('#backup-preview-content');
  if (sensitiveButton) sensitiveButton.style.display = currentUser?.role === 'MASTER' && !sensitive ? '' : 'none';
  modal.style.display = 'flex';
  setMessage(message, sensitive ? '민감정보 포함 미리보기를 불러오는 중...' : '미리보기를 불러오는 중...');
  if (content) content.innerHTML = '';
  try {
    const preview = await apiRequest(`/data/backups/${backupId}/preview?sensitive=${sensitive ? 'true' : 'false'}`);
    renderPreview(preview);
    setMessage(message, '');
  } catch (e) {
    setMessage(message, e.message || '백업 미리보기를 불러오지 못했습니다.', true);
  }
}

function renderRestoreJobs(jobs) {
  if (!restoreList) return;
  if (!jobs.length) {
    restoreList.innerHTML = '<tr><td colspan="7" class="muted">복원 이력이 없습니다.</td></tr>';
    return;
  }
  restoreList.innerHTML = '';
  jobs.forEach((job) => {
    const tr = document.createElement('tr');
    const rollbackButton = canRollbackJob(job)
      ? `<button class="btn tiny danger" type="button" data-rollback="${job.id}">복원 전 상태로 되돌리기</button>`
      : '-';
    tr.innerHTML = `
      <td>${escapeHtml(job.mode)}</td>
      <td>${escapeHtml(job.status)}</td>
      <td>${job.started_at ? formatDateTimeSeoul(job.started_at) : '-'}</td>
      <td>${job.finished_at ? formatDateTimeSeoul(job.finished_at) : '-'}</td>
      <td>${restorePointHtml(job)}</td>
      <td>${escapeHtml(job.error_message || '-')}</td>
      <td>${rollbackButton}</td>
    `;
    restoreList.appendChild(tr);
  });
}

function canRegisterStorageFile(item) {
  const validation = storageValidationState.get(item.storage_key);
  return !item.registered && item.status === 'UNREGISTERED' && validation?.valid && validation.domain === domain;
}

function isRestorePointItem(item) {
  return (item.kind || '').toUpperCase() === 'RESTORE_POINT';
}

function storageKindHtml(item) {
  if (!isRestorePointItem(item)) return escapeHtml(item.kind || '-');
  return `
    <span class="badge pending">RESTORE_POINT</span>
    <div class="muted small">복원 전 자동 생성 지점</div>
  `;
}

function ensureStorageRestorePointControls() {
  if (!storageBackupList || document.getElementById('storage-restore-point-toggle')) return;
  const tableWrap = storageBackupList.closest('.table-wrap');
  const section = tableWrap?.parentElement;
  if (!section) return;
  const note = document.createElement('div');
  note.className = 'form-message show';
  note.innerHTML = '복원 지점은 복원 실행 직전에 자동 생성되는 백업이며, 되돌리기 용도로 사용됩니다. 일반 백업 목록에는 표시되지 않습니다.';
  const controls = document.createElement('label');
  controls.className = 'inline-input';
  controls.innerHTML = '<input id="storage-restore-point-toggle" type="checkbox" checked /> 복원 지점 파일도 표시';
  section.insertBefore(note, tableWrap);
  section.insertBefore(controls, tableWrap);
  controls.querySelector('#storage-restore-point-toggle')?.addEventListener('change', (event) => {
    showRestorePointFiles = event.target.checked;
    renderStorageBackups(lastStorageItems);
  });
}

function renderStorageBackups(items) {
  if (!storageBackupList) return;
  ensureStorageRestorePointControls();
  lastStorageItems = items || [];
  const visibleItems = showRestorePointFiles ? lastStorageItems : lastStorageItems.filter((item) => !isRestorePointItem(item));
  if (!visibleItems.length) {
    storageBackupList.innerHTML = '<tr><td colspan="8" class="muted">저장소 백업 파일이 없습니다.</td></tr>';
    return;
  }
  storageBackupList.innerHTML = '';
  visibleItems.forEach((item) => {
    const tr = document.createElement('tr');
    const validation = storageValidationState.get(item.storage_key);
    const validationText = validation
      ? (validation.valid ? '정상' : '실패')
      : (item.errors?.length ? '오류 있음' : '-');
    const registerDisabled = canRegisterStorageFile(item) ? '' : 'disabled';
    const registerButton = item.registered
      ? '<button class="btn tiny secondary" type="button" disabled>등록됨</button>'
      : `<button class="btn tiny" type="button" data-storage-register="${escapeHtml(item.storage_key)}" ${registerDisabled}>백업 목록에 등록</button>`;
    tr.innerHTML = `
      <td title="${escapeHtml(item.display_path)}">${escapeHtml(item.file_name)}</td>
      <td>${escapeHtml(item.status)}</td>
      <td>${storageKindHtml(item)}</td>
      <td>${formatSize(item.file_size)}</td>
      <td>${item.modified_at ? formatDateTimeSeoul(item.modified_at) : '-'}</td>
      <td>${escapeHtml(item.schema_version || '-')}</td>
      <td>${escapeHtml(validationText)}</td>
      <td>
        <button class="btn tiny secondary" type="button" data-storage-validate="${escapeHtml(item.storage_key)}">검증</button>
        ${registerButton}
      </td>
    `;
    storageBackupList.appendChild(tr);
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

async function loadStorageBackups() {
  if (!storageBackupList) return;
  const result = await apiRequest(`/data/backups/storage?domain=${encodeURIComponent(domain)}`);
  renderStorageBackups(result.items || []);
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
    await loadStorageBackups();
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

restoreList?.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-rollback]');
  if (!button) return;
  const warning = [
    '이 작업은 해당 복원 직전 상태로 데이터를 다시 교체합니다.',
    '현재 상태도 되돌리기 전 백업으로 자동 저장됩니다.',
    domain === 'FULL' ? 'FULL 되돌리기는 계정과 권한 정보가 바뀔 수 있으므로 완료 후 재로그인이 필요할 수 있습니다.' : null,
    '진행하려면 "되돌립니다"를 입력하세요.',
  ].filter(Boolean).join('\n');
  const confirmText = window.prompt(warning);
  if (confirmText !== '되돌립니다') {
    setMessage(validationResult, '되돌리기 확인 문구가 일치하지 않습니다.', true);
    return;
  }
  button.disabled = true;
  setMessage(validationResult, '복원 전 상태로 되돌리는 중...');
  try {
    const job = await apiRequest(`/data/restores/${button.dataset.rollback}/rollback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm_text: confirmText }),
    });
    setMessage(validationResult, `되돌리기 완료: ${job.status}`);
    await loadBackups();
    await loadRestoreJobs();
    await loadStorageBackups();
  } catch (e) {
    setMessage(validationResult, e.message || '되돌리기에 실패했습니다.', true);
  } finally {
    button.disabled = false;
  }
});

backupList?.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-preview], [data-download], [data-validate], [data-restore], [data-exclude]');
  if (!button) return;
  button.disabled = true;
  try {
    if (button.dataset.preview) {
      await openPreview(button.dataset.preview);
    } else if (button.dataset.download) {
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
      if (config.restorable === false) {
        setMessage(validationResult, config.restoreWarning, true);
        return;
      }
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
    } else if (button.dataset.exclude) {
      if (!window.confirm('이 백업을 백업 목록에서 제외합니다. 실제 저장소 파일은 삭제되지 않습니다.')) {
        return;
      }
      setMessage(backupMessage, '백업을 목록에서 제외하는 중...');
      await apiRequest(`/data/backups/${button.dataset.exclude}`, { method: 'DELETE' });
      validationState.delete(button.dataset.exclude);
      setMessage(backupMessage, '백업 목록에서 제외했습니다. 저장소 파일은 유지됩니다.');
      await loadBackups();
      await loadStorageBackups();
    }
  } catch (e) {
    setMessage(validationResult, e.message || '요청 처리에 실패했습니다.', true);
  } finally {
    button.disabled = false;
  }
});

storageBackupList?.addEventListener('click', async (event) => {
  const button = event.target.closest('[data-storage-validate], [data-storage-register]');
  if (!button) return;
  button.disabled = true;
  const storageKey = button.dataset.storageValidate || button.dataset.storageRegister;
  try {
    if (button.dataset.storageValidate) {
      setMessage(storageMessage, '저장소 백업 파일 검증 중...');
      const result = await apiRequest('/data/backups/storage/validate', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain, storage_key: storageKey }),
      });
      storageValidationState.set(storageKey, result);
      renderValidation(result, storageMessage);
      await loadStorageBackups();
    } else if (button.dataset.storageRegister) {
      const validation = storageValidationState.get(storageKey);
      if (!validation?.valid || validation.domain !== domain) {
        setMessage(storageMessage, '저장소 백업 파일을 먼저 검증하세요.', true);
        return;
      }
      const description = window.prompt('백업 목록에 등록할 설명을 입력하세요.', 'Storage file re-registered');
      if (description === null) return;
      setMessage(storageMessage, '저장소 백업 파일 등록 중...');
      await apiRequest('/data/backups/storage/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ domain, storage_key: storageKey, description }),
      });
      storageValidationState.delete(storageKey);
      setMessage(storageMessage, '저장소 백업 파일을 백업 목록에 등록했습니다.');
      await loadBackups();
      await loadStorageBackups();
    }
  } catch (e) {
    setMessage(storageMessage, e.message || '저장소 백업 파일 요청에 실패했습니다.', true);
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
const createBackupButton = document.getElementById('create-backup');
if (createBackupButton && config.createLabel) createBackupButton.textContent = config.createLabel;
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

try {
  await loadStorageBackups();
} catch (e) {
  if (storageBackupList) storageBackupList.innerHTML = '<tr><td colspan="8" class="error">저장소 백업 파일을 불러오지 못했습니다.</td></tr>';
  setMessage(storageMessage, e.message || '저장소 백업 파일을 불러오지 못했습니다.', true);
}

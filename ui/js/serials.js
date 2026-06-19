// File: /ui/js/serials.js
import { apiRequest } from './api.js';
import { loadUser } from './auth.js';

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, (ch) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[ch]));
}

// --- Global State ---
let currentRole = null;
let serials = [];
let layouts = [];
let shelfTypes = [];
let shelves = [];

// Edit State (Manage Page)
let editingSerialId = null;

// Editor State (Layout Page)
let currentLayout = null;
let currentMode = 'select'; // 'select'|'wall'
let editorScale = 1.0;
let editorPan = { x: 0, y: 0 };
const UNIT_SIZE = 10;
const GRID_SIZE = 10;

let selectedElement = null;
let dragOffset = { x: 0, y: 0 };
let panStart = { x: 0, y: 0 };

// Palette Drag State
let paletteDragItem = null;

const acquisitionLabels = {
  UNCLASSIFIED: '미분류',
  DONATION: '수증',
  SUBSCRIPTION: '구독'
};

// --- Initialization ---
export async function initSerials() {
  try {
    const user = await loadUser();
    currentRole = user?.role || null;
    applyRoleGuard();

    const isEditor = !!document.getElementById('editor-toolbar');
    const isManage = !!document.getElementById('serial-form');
    const isHomeOrList = !isEditor && !isManage && (!!document.getElementById('serials-total-count') || !!document.getElementById('serials-table'));

    await Promise.allSettled([loadLayouts(), loadShelfTypes()]);

    if (isHomeOrList || isManage) {
      await loadSerials().catch(console.error);
      if (layouts.length > 0) {
        await loadShelves(layouts[0].id).catch(console.error);
      }
    }

    if (isHomeOrList) {
      if (layouts.length > 0) await selectLayout(layouts[0].id, false);
      bindListEvents();
      renderStats();
      renderList();
      renderLayoutLegend();
      bindHomeCanvasEvents(); // Home page zoom
    }

    if (isManage) {
      renderStats();
      renderList();
      renderShelfOptions();
      bindManageEvents();
    }

    if (isEditor) {
      currentMode = 'select';
      renderLayoutSelect();
      renderShelfPalette();

      if (layouts.length > 0) {
        await selectLayout(layouts[0].id, true);
      } else {
        showEmptyState();
      }
      bindToolbarEvents();
      bindDialogEvents();

      document.querySelectorAll('dialog').forEach(d => {
        try { d.close(); } catch (e) { }
        d.style.display = 'none';
      });
    }

    bindCanvasEvents(isEditor);
    bindSidebarEvents();

  } catch (err) {
    console.warn("Init warning:", err);
  }
}

function applyRoleGuard() {
  const isOperator = currentRole === 'OPERATOR' || currentRole === 'MASTER';
  document.body.classList.toggle('role-operator', isOperator);

  const protectedBtns = document.querySelectorAll('#layout-create-btn, #layout-delete-btn, #save-layout-btn, #manage-types-btn, .action-btn');
  protectedBtns.forEach(btn => btn.disabled = !isOperator);

  const form = document.getElementById('serial-form');
  if (form && !isOperator) {
    form.querySelectorAll('input, select, textarea, button').forEach(el => el.disabled = true);
    const permEl = document.getElementById('serials-permission');
    if (permEl) permEl.style.display = 'block';
  }
}

// --- Data Loading ---
async function loadLayouts() {
  layouts = await apiRequest('/serials/layouts');
}

async function loadShelfTypes() {
  shelfTypes = await apiRequest('/serials/shelf-types');
}

async function loadShelves(layoutId) {
  if (!layoutId) return [];
  shelves = await apiRequest(`/serials/shelves?layout_id=${layoutId}`);
}

async function loadSerials() {
  const query = buildQuery();
  const url = query ? `/serials?${query}` : '/serials';
  serials = await apiRequest(url);
}

function buildQuery() {
  const params = new URLSearchParams();
  const keyword = document.getElementById('search-keyword')?.value?.trim() ?? '';
  const issn = document.getElementById('search-issn')?.value?.trim() ?? '';
  const shelf = document.getElementById('search-shelf')?.value?.trim() ?? '';
  const type = document.getElementById('search-type')?.value ?? '';
  if (keyword) params.set('q', keyword);
  if (issn) params.set('issn', issn);
  if (shelf) params.set('shelf_section', shelf);
  if (type) params.set('acquisition_type', type);
  return params.toString();
}
const COLOR_PALETTE = ['#3b82f6', '#22c55e', '#f59e0b', '#ec4899', '#6366f1', '#a855f7', '#14b8a6', '#ef4444'];

function getShelfTypeColor(index, shelfType = null) {
  return shelfType?.color || COLOR_PALETTE[index >= 0 ? index % COLOR_PALETTE.length : 0];
}

function markLayoutDirty(message = '저장 버튼을 눌러 배치도 변경사항을 반영하세요.') {
  const el = document.getElementById('canvas-status-text');
  if (el) el.textContent = message;
}

// Hex 색상에 투명도 적용
function hexToRgba(hex, alpha) {
  let c;
  if (/^#([A-Fa-f0-9]{3}){1,2}$/.test(hex)) {
    c = hex.substring(1).split('');
    if (c.length === 3) {
      c = [c[0], c[0], c[1], c[1], c[2], c[2]];
    }
    c = '0x' + c.join('');
    return 'rgba(' + [(c >> 16) & 255, (c >> 8) & 255, c & 255].join(',') + ',' + alpha + ')';
  }
  return hex;
}
// --- Manage Page Logic ---
function renderShelfOptions() {
  const select = document.getElementById('serial-shelf-id');
  if (!select) return;
  select.innerHTML = '<option value="">배치도에서 선택 (또는 직접 입력)</option>' +
    shelves.map(s => `<option value="${escapeHtml(s.id)}">${escapeHtml(s.code)}</option>`).join('');
}

function bindManageEvents() {
  const form = document.getElementById('serial-form');
  if (!form) return;

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    const payload = {
      title: document.getElementById('serial-title').value,
      issn: document.getElementById('serial-issn').value,
      acquisition_type: document.getElementById('serial-type').value,
      shelf_section: document.getElementById('serial-shelf').value,
      shelf_id: document.getElementById('serial-shelf-id').value || null,
      shelf_row: parseInt(document.getElementById('serial-row').value) || null,
      shelf_column: parseInt(document.getElementById('serial-column').value) || null,
      shelf_row_end: parseInt(document.getElementById('serial-row-end').value) || null,
      shelf_column_end: parseInt(document.getElementById('serial-column-end').value) || null,
      shelf_note: document.getElementById('serial-note').value,
      remark: document.getElementById('serial-remark').value
    };

    // POST: /serials (without /publications)
    // PUT: /serials/publications/{id}
    const url = editingSerialId ? `/serials/publications/${editingSerialId}` : '/serials';
    const method = editingSerialId ? 'PUT' : 'POST';

    try {
      await apiRequest(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
      alert('저장되었습니다.');
      await loadSerials();
      renderList();
      resetManageForm();
    } catch (err) {
      console.error(err);
      alert('저장 오류: ' + err.message);
    }
  });

  document.getElementById('serial-new')?.addEventListener('click', resetManageForm);
  document.getElementById('serial-delete')?.addEventListener('click', async () => {
    if (!editingSerialId) return;
    if (confirm('삭제하시겠습니까?')) {
      try {
        await apiRequest(`/serials/publications/${editingSerialId}`, { method: 'DELETE' });
        await loadSerials();
        renderList();
        resetManageForm();
      } catch (err) {
        console.error(err);
        alert('삭제 오류: ' + err.message);
      }
    }
  });
}

function resetManageForm() {
  editingSerialId = null;
  document.getElementById('serial-form').reset();
  const delBtn = document.getElementById('serial-delete');
  if (delBtn) delBtn.style.display = 'none';
  document.querySelectorAll('#serials-table tbody tr').forEach(r => r.classList.remove('active', 'selected'));
}

function populateManageForm(serial) {
  editingSerialId = serial.id;
  const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.value = val || ''; };

  setVal('serial-title', serial.title);
  setVal('serial-issn', serial.issn);
  setVal('serial-type', serial.acquisition_type);
  setVal('serial-shelf', serial.shelf_section);
  setVal('serial-shelf-id', serial.shelf_id);
  setVal('serial-row', serial.shelf_row);
  setVal('serial-column', serial.shelf_column);
  setVal('serial-row-end', serial.shelf_row_end);
  setVal('serial-column-end', serial.shelf_column_end);
  setVal('serial-note', serial.shelf_note);
  setVal('serial-remark', serial.remark);

  const delBtn = document.getElementById('serial-delete');
  if (delBtn) delBtn.style.display = 'inline-block';
}


// --- List & Stats ---
function renderStats() {
  if (!document.getElementById('serials-total-count')) return;
  document.getElementById('serials-total-count').textContent = serials.length.toLocaleString();
  document.getElementById('serials-donation-count').textContent = serials.filter(s => s.acquisition_type === 'DONATION').length.toLocaleString();
  document.getElementById('serials-subscription-count').textContent = serials.filter(s => s.acquisition_type === 'SUBSCRIPTION').length.toLocaleString();
}

function renderList() {
  const tbody = document.getElementById('serials-table')?.querySelector('tbody');
  if (!tbody) return;
  tbody.innerHTML = '';

  if (serials.length === 0) {
    tbody.innerHTML = '<tr><td colspan="4" class="muted center">데이터가 없습니다.</td></tr>';
    return;
  }

  const isManage = !!document.getElementById('serial-form');

  serials.forEach(s => {
    const tr = document.createElement('tr');
    tr.innerHTML = `
         <td>${escapeHtml(s.title)}</td>
         <td>${escapeHtml(s.issn || '-')}</td>
         <td>${escapeHtml(acquisitionLabels[s.acquisition_type] || s.acquisition_type)}</td>
         <td>${escapeHtml(formatShelfLabel(s))}</td>
       `;
    tr.addEventListener('click', () => {
      tbody.querySelectorAll('tr').forEach(r => r.classList.remove('active', 'selected'));
      tr.classList.add('active', 'selected');

      if (isManage) {
        populateManageForm(s);
      } else {
        showSerialDetail(s);
      }
    });
    if (s.id === editingSerialId) {
      tr.classList.add('active', 'selected');
    }
    tbody.appendChild(tr);
  });

  const status = document.getElementById('serials-status');
  if (status) status.textContent = `${serials.length}건 표시 중`;
}

function showSerialDetail(serial) {
  const setText = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
  setText('detail-title', serial.title);
  setText('detail-issn', serial.issn || '-');
  setText('detail-type', acquisitionLabels[serial.acquisition_type] || serial.acquisition_type);
  setText('detail-shelf', formatShelfLabel(serial));

  // 위치 정보 포맷팅
  let locationText = '-';
  if (serial.shelf_row && serial.shelf_column) {
    if (serial.shelf_row_end && serial.shelf_column_end) {
      locationText = `${serial.shelf_row}행 ${serial.shelf_column}칸 ~ ${serial.shelf_row_end}행 ${serial.shelf_column_end}칸`;
    } else {
      locationText = `${serial.shelf_row}행 ${serial.shelf_column}칸`;
    }
  }
  setText('detail-location', locationText);
  setText('detail-note', serial.shelf_note || serial.remark || '-');

  // 평면도에서 서가 하이라이트 및 확대
  if (serial.shelf_id) {
    const shelf = shelves.find(s => s.id === serial.shelf_id);
    if (shelf) {
      selectElement('shelf', shelf);
      highlightAndZoomToShelf(shelf);
    }
  }

  // 서가 시각화 렌더링
  renderShelfVisual(serial);
}

function formatShelfLabel(serial) {
  if (serial.shelf_code) return serial.shelf_code;
  const shelf = shelves.find(s => s.id === serial.shelf_id);
  return shelf ? shelf.code : (serial.shelf_section || '-');
}

function bindListEvents() {
  document.getElementById('search-button')?.addEventListener('click', async () => {
    await loadSerials(); renderList();
  });
  document.getElementById('search-reset')?.addEventListener('click', async () => {
    ['search-keyword', 'search-issn', 'search-shelf'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
    const typeEl = document.getElementById('search-type'); if (typeEl) typeEl.value = '';
    await loadSerials(); renderList();
  });
}

function renderLayoutLegend() {
  const legend = document.getElementById('layout-legend');
  if (!legend) return;
  legend.innerHTML = shelfTypes.map((t, idx) => {
    const color = getShelfTypeColor(idx, t);
    return `<div class="legend-item"><span class="legend-swatch" style="background:${escapeHtml(color)}"></span>${escapeHtml(t.name)}</div>`;
  }).join('');
}

// --- 서가 시각화 ---
function renderShelfVisual(serial) {
  const container = document.getElementById('shelf-visual');
  if (!container) return;

  if (!serial.shelf_id) {
    container.innerHTML = '<div class="shelf-visual-empty">배치 서가가 지정되지 않았습니다</div>';
    return;
  }

  const shelf = shelves.find(s => s.id === serial.shelf_id);
  if (!shelf) {
    container.innerHTML = '<div class="shelf-visual-empty">서가 정보를 찾을 수 없습니다</div>';
    return;
  }

  const shelfType = shelfTypes.find(t => t.id === shelf.shelf_type_id);
  if (!shelfType) {
    container.innerHTML = '<div class="shelf-visual-empty">서가 타입 정보를 찾을 수 없습니다</div>';
    return;
  }

  const rows = shelfType.rows || 5;
  const cols = shelfType.columns || 3;

  // 색상 배정
  const typeIndex = shelfTypes.findIndex(t => String(t.id).toLowerCase().trim() === String(shelfType.id).toLowerCase().trim());
  const color = getShelfTypeColor(typeIndex, shelfType);

  // 시작/종료 위치 (기본값: 동일한 셀)
  const startRow = serial.shelf_row || 0;
  const startCol = serial.shelf_column || 0;
  const endRow = serial.shelf_row_end || startRow;
  const endCol = serial.shelf_column_end || startCol;

  let html = `<div class="shelf-visual-header">${shelf.code} (${rows}행 × ${cols}칸)</div>`;
  html += `<div class="shelf-grid" style="grid-template-columns: repeat(${cols}, 1fr);">`;

  for (let r = 1; r <= rows; r++) {
    for (let c = 1; c <= cols; c++) {
      const isHighlighted = r >= startRow && r <= endRow && c >= startCol && c <= endCol && startRow > 0;
      // 하이라이트된 셀에 동적 색상 적용
      let style = '';
      if (isHighlighted) {
        style = `background-color: ${escapeHtml(color)}; border-color: ${escapeHtml(color)}; color: white; font-weight: 600;`;
      }

      html += `<div class="shelf-cell${isHighlighted ? ' highlighted' : ''}" 
                    style="${style}"
                    data-row="${r}" data-col="${c}" 
                    ${isHighlighted ? `data-label="${r}-${c}"` : ''}>${r}-${c}</div>`;
    }
  }

  html += '</div>';
  container.innerHTML = html;
}

// --- 평면도 확대/하이라이트 ---
function highlightAndZoomToShelf(shelf) {
  const canvasEl = document.getElementById('layout-canvas');
  if (!canvasEl || !currentLayout) return;

  // 해당 서가 찾아서 확대
  const shelfType = shelfTypes.find(t => t.id === shelf.shelf_type_id);
  if (!shelfType) return;

  // 서가 실제 크기 계산 (칸 수 기반)
  const shelfWidth = (shelfType.columns || 4) * UNIT_SIZE;
  const shelfHeight = 2 * UNIT_SIZE;
  const shelfCenterX = shelf.x + shelfWidth / 2;
  const shelfCenterY = shelf.y + shelfHeight / 2;

  // 캔버스 크기
  const containerRect = canvasEl.getBoundingClientRect();

  // 확대 스케일 설정 (2배로 더 확대)
  editorScale = 2.0;

  // 서가가 캔버스 중앙에 오도록 위치 조정
  editorPan.x = (containerRect.width / 2) - (shelfCenterX * editorScale);
  editorPan.y = (containerRect.height / 2) - (shelfCenterY * editorScale);

  // 캔버스 재렌더링
  renderCanvas();
}

// --- 홈 배치도 말풍선 ---
let currentTooltip = null;

function showShelfTooltip(shelf, x, y, canvasEl) {
  hideShelfTooltip();

  const shelfSerials = serials.filter(s => s.shelf_id === shelf.id);
  const shelfType = shelfTypes.find(t => t.id === shelf.shelf_type_id);

  let html = `
    <div class="shelf-tooltip" style="left: ${x}px; top: ${y}px;">
      <button class="shelf-tooltip-close" onclick="this.parentElement.remove()">×</button>
      <div class="shelf-tooltip-header">${shelf.code}${shelfType ? ` (${shelfType.rows}행 × ${shelfType.columns}칸)` : ''}</div>
  `;

  if (shelfSerials.length === 0) {
    html += '<div class="shelf-tooltip-empty">등록된 간행물이 없습니다</div>';
  } else {
    html += '<div class="shelf-tooltip-list">';
    shelfSerials.forEach(s => {
      let loc = '';
      if (s.shelf_row && s.shelf_column) {
        loc = `${s.shelf_row}행 ${s.shelf_column}칸`;
        if (s.shelf_row_end && s.shelf_column_end) {
          loc += ` ~ ${s.shelf_row_end}행 ${s.shelf_column_end}칸`;
        }
      }
      html += `
        <div class="shelf-tooltip-item">
          <div class="title">${s.title}</div>
          ${loc ? `<div class="location">${loc}</div>` : ''}
        </div>`;
    });
    html += '</div>';

    // 미니 서가 시각화
    if (shelfType) {
      const rows = shelfType.rows || 5;
      const cols = shelfType.columns || 3;
      // 색상 배정
      const typeIndex = shelfTypes.findIndex(t => String(t.id).toLowerCase().trim() === String(shelfType.id).toLowerCase().trim());
      const color = getShelfTypeColor(typeIndex, shelfType);

      html += `<div class="tooltip-shelf-grid" style="grid-template-columns: repeat(${cols}, 1fr); border-color: ${escapeHtml(color)}; background-color: ${hexToRgba(color, 0.1)};">`;
      for (let r = 1; r <= rows; r++) {
        for (let c = 1; c <= cols; c++) {
          // 해당 셀에 간행물이 있는지 확인
          const isOccupied = shelfSerials.some(s => {
            const startRow = s.shelf_row || 0;
            const startCol = s.shelf_column || 0;
            const endRow = s.shelf_row_end || startRow;
            const endCol = s.shelf_column_end || startCol;
            return r >= startRow && r <= endRow && c >= startCol && c <= endCol;
          });

          let cellStyle = `border-color: ${hexToRgba(color, 0.3)};`;
          if (isOccupied) {
            cellStyle += `background-color: ${escapeHtml(color)};`; // Occupied cells use solid color
          }

          html += `<div class="tooltip-shelf-cell${isOccupied ? ' occupied' : ''}" style="${cellStyle}"></div>`;
        }
      }
      html += '</div>';
    }
  }

  html += '</div>';

  const tooltip = document.createElement('div');
  tooltip.innerHTML = html;
  canvasEl.appendChild(tooltip.firstElementChild);
  currentTooltip = canvasEl.querySelector('.shelf-tooltip');
}

function hideShelfTooltip() {
  if (currentTooltip) {
    currentTooltip.remove();
    currentTooltip = null;
  }
  document.querySelectorAll('.shelf-tooltip').forEach(t => t.remove());
}


// --- Canvas Rendering ---
async function renderCanvas() {
  const canvasEl = document.getElementById('layout-canvas');
  if (!canvasEl) return;
  if (!currentLayout) {
    canvasEl.innerHTML = '<div class="muted center-message">배치도가 없습니다.</div>';
    return;
  }

  canvasEl.innerHTML = '';

  const width = currentLayout.width || 800;
  const height = currentLayout.height || 600;
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('width', '100%');
  svg.setAttribute('height', '100%');
  svg.addEventListener('contextmenu', e => e.preventDefault());

  // Root Group for Pan/Zoom
  const rootGroup = document.createElementNS(ns, 'g');
  rootGroup.setAttribute('transform', `translate(${editorPan.x}, ${editorPan.y}) scale(${editorScale})`);
  rootGroup.id = 'canvas-root';

  // Grid Pattern
  const defs = document.createElementNS(ns, 'defs');
  const pattern = document.createElementNS(ns, 'pattern');
  pattern.id = 'grid';
  pattern.setAttribute('width', GRID_SIZE);
  pattern.setAttribute('height', GRID_SIZE);
  pattern.setAttribute('patternUnits', 'userSpaceOnUse');
  const path = document.createElementNS(ns, 'path');
  path.setAttribute('d', `M ${GRID_SIZE} 0 L 0 0 0 ${GRID_SIZE}`);
  path.setAttribute('fill', 'none');
  path.setAttribute('stroke', '#e2e8f0');
  path.setAttribute('stroke-width', '0.5');
  pattern.appendChild(path);
  defs.appendChild(pattern);
  svg.appendChild(defs);

  // Background (Outside Layout)
  const bgRect = document.createElementNS(ns, 'rect');
  bgRect.setAttribute('x', -5000); bgRect.setAttribute('y', -5000);
  bgRect.setAttribute('width', 10000); bgRect.setAttribute('height', 10000);
  bgRect.setAttribute('fill', '#f1f5f9');
  rootGroup.appendChild(bgRect);

  // Layout Area (White with Grid)
  const layoutRect = document.createElementNS(ns, 'rect');
  layoutRect.setAttribute('x', 0);
  layoutRect.setAttribute('y', 0);
  layoutRect.setAttribute('width', width);
  layoutRect.setAttribute('height', height);
  layoutRect.setAttribute('fill', 'white');
  rootGroup.appendChild(layoutRect);

  const layoutGrid = document.createElementNS(ns, 'rect');
  layoutGrid.setAttribute('x', 0); layoutGrid.setAttribute('y', 0);
  layoutGrid.setAttribute('width', width); layoutGrid.setAttribute('height', height);
  layoutGrid.setAttribute('fill', 'url(#grid)');
  layoutGrid.setAttribute('pointer-events', 'none');
  rootGroup.appendChild(layoutGrid);

  // Dotted Boundary
  const border = document.createElementNS(ns, 'rect');
  border.setAttribute('x', 0); border.setAttribute('y', 0);
  border.setAttribute('width', width);
  border.setAttribute('height', height);
  border.setAttribute('fill', 'none');
  border.setAttribute('stroke', '#94a3b8');
  border.setAttribute('stroke-width', '1');
  border.setAttribute('stroke-dasharray', '5,5');
  border.setAttribute('pointer-events', 'none');
  rootGroup.appendChild(border);

  const contentGroup = document.createElementNS(ns, 'g');
  contentGroup.id = 'canvas-content';

  // Walls
  (currentLayout.walls || []).forEach((wall, idx) => {
    const line = document.createElementNS(ns, 'line');
    line.setAttribute('x1', wall.x1);
    line.setAttribute('y1', wall.y1);
    line.setAttribute('x2', wall.x2);
    line.setAttribute('y2', wall.y2);
    line.classList.add('wall-line');
    if ((selectedElement?.type === 'wall' && selectedElement.index === idx) || isElementInSelection({ x1: wall.x1, y1: wall.y1, x2: wall.x2, y2: wall.y2, index: idx }, 'wall')) {
      line.classList.add('selected');
    }
    line.dataset.index = idx;
    contentGroup.appendChild(line);
  });

  // Ensure shelfTypes are loaded
  if (!shelfTypes || shelfTypes.length === 0) {
    console.debug('renderCanvas: shelfTypes empty, reloading...');
    await loadShelfTypes();
    if (!shelfTypes || shelfTypes.length === 0) {
      console.warn('renderCanvas: Failed to load shelfTypes');
    }
  }

  // Shelves
  shelves.forEach(shelf => {
    const g = document.createElementNS(ns, 'g');
    const rotation = shelf.rotation || 0;
    g.setAttribute('transform', `translate(${escapeHtml(shelf.x)}, ${escapeHtml(shelf.y)}) rotate(${rotation})`);
    g.classList.add('shelf-group');
    if ((selectedElement?.type === 'shelf' && selectedElement.id === shelf.id) || isElementInSelection(shelf, 'shelf')) {
      g.classList.add('selected');
    }
    g.dataset.id = shelf.id;

    const normalizeId = (id) => String(id || '').toLowerCase().trim();
    const typeIndex = shelfTypes.findIndex(t => normalizeId(t.id) === normalizeId(shelf.shelf_type_id));
    const type = shelfTypes[typeIndex] || { rows: 4, columns: 4 }; // Use index directly if found
    const shelfWidth = (type.columns || 4) * UNIT_SIZE;
    const shelfHeight = 2 * UNIT_SIZE; // Fixed height: 2 grid units

    // 서가 타입별 자동 색상 배정 (타입 순서에 따라 팔레트에서 선택)
    const color = getShelfTypeColor(typeIndex, type);
    const borderColor = color;
    const fillColor = color; // Solid color to match legend
    const textColor = '#ffffff'; // White text on solid background

    const rect = document.createElementNS(ns, 'rect');
    rect.setAttribute('width', shelfWidth);
    rect.setAttribute('height', shelfHeight);
    rect.setAttribute('fill', fillColor);
    rect.setAttribute('stroke', borderColor);
    rect.setAttribute('stroke-width', '2');
    rect.setAttribute('rx', '4');

    const text = document.createElementNS(ns, 'text');
    text.setAttribute('x', shelfWidth / 2);
    text.setAttribute('y', shelfHeight / 2 + 3);
    text.setAttribute('text-anchor', 'middle');
    text.setAttribute('font-size', '9');
    text.setAttribute('fill', textColor);
    text.setAttribute('font-weight', '600');
    text.textContent = shelf.code;

    g.appendChild(rect);
    g.appendChild(text);
    contentGroup.appendChild(g);
  });

  // Active Drawing Line (SOLID, not dashed)
  if (isDrawing && activeLine) {
    contentGroup.appendChild(activeLine);
  }

  // Selection Box
  if (isSelectingArea && selectionRect) {
    const r = document.createElementNS(ns, 'rect');
    r.setAttribute('x', selectionRect.x);
    r.setAttribute('y', selectionRect.y);
    r.setAttribute('width', selectionRect.width);
    r.setAttribute('height', selectionRect.height);
    r.setAttribute('fill', 'rgba(59, 130, 246, 0.15)');
    r.setAttribute('stroke', '#3b82f6');
    r.setAttribute('stroke-width', '1');
    contentGroup.appendChild(r);
  }

  rootGroup.appendChild(contentGroup);
  svg.appendChild(rootGroup);
  canvasEl.appendChild(svg);
}


// --- Interaction Logic ---
let isDrawing = false;
let isPanning = false;
let isSelectingArea = false;
let startPoint = null;
let activeLine = null;
let isDraggingShelf = false;
let draggingShelf = null;
let selectionRect = null;

function bindCanvasEvents(editorMode) {
  const canvasEl = document.getElementById('layout-canvas');
  if (!canvasEl) return;

  // Prevent browser context menu on canvas
  canvasEl.addEventListener('contextmenu', (e) => {
    e.preventDefault();
  });

  // Wheel Zoom (마우스 위치 기준 확대/축소)
  canvasEl.addEventListener('wheel', (e) => {
    e.preventDefault();
    const delta = e.deltaY;
    const zoomStep = 0.1;
    const oldScale = editorScale;
    const newScale = Math.max(0.2, Math.min(5, oldScale + (delta > 0 ? -zoomStep : zoomStep)));

    // 마우스 위치 기준으로 확대/축소
    const rect = canvasEl.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    // 현재 마우스 위치의 월드 좌표
    const worldX = (mouseX - editorPan.x) / oldScale;
    const worldY = (mouseY - editorPan.y) / oldScale;

    // 새 스케일 적용 후 동일한 월드 좌표가 마우스 위치에 오도록 pan 조정
    editorPan.x = mouseX - worldX * newScale;
    editorPan.y = mouseY - worldY * newScale;
    editorScale = newScale;

    renderCanvas();
  });

  canvasEl.addEventListener('mousedown', (e) => {
    if (!currentLayout) return;

    // Right click for Area Selection
    if (e.button === 2 && editorMode) {
      isSelectingArea = true;
      const pt = getWorldCoordinates(e, canvasEl);
      startPoint = pt;
      selectionRect = { x: pt.x, y: pt.y, width: 0, height: 0 };
      return;
    }

    const target = e.target.closest('.wall-line, .shelf-group');

    if (editorMode && currentMode === 'wall' && e.button === 0) {
      const worldPt = getWorldCoordinates(e, canvasEl);
      if (worldPt.x < 0 || worldPt.y < 0 || worldPt.x > currentLayout.width || worldPt.y > currentLayout.height) {
        return;
      }

      e.preventDefault();
      isDrawing = true;
      isPanning = false;
      isDraggingShelf = false;
      const snapX = Math.round(worldPt.x / GRID_SIZE) * GRID_SIZE;
      const snapY = Math.round(worldPt.y / GRID_SIZE) * GRID_SIZE;
      startPoint = { x: snapX, y: snapY };
      activeLine = createSVGLine(snapX, snapY, snapX, snapY, ['wall-line', 'preview']);
      renderCanvas();
      return;
    }

    // Pan: Middle Click OR Left Click on Empty Background (조회 페이지에서도 작동)
    if (e.button === 1 || (!target && e.button === 0)) {
      isPanning = true;
      panStart = { x: e.clientX, y: e.clientY };
      canvasEl.style.cursor = 'grabbing';
      return;
    }

    if (editorMode && e.button === 0) {
      if (currentMode === 'select') {
        if (target) {
          if (target.classList.contains('wall-line')) {
            selectElement('wall', { index: parseInt(target.dataset.index), data: currentLayout.walls[parseInt(target.dataset.index)] });
          } else if (target.classList.contains('shelf-group')) {
            const shelf = shelves.find(s => s.id === target.dataset.id);
            selectElement('shelf', shelf);
            startShelfDrag(e, shelf, canvasEl);
          }
        } else {
          selectElement(null);
        }
      }
    }
    // 홈/조회 페이지: 서가 클릭 시 말풍선 표시
    if (!editorMode && e.button === 0 && target && target.classList.contains('shelf-group')) {
      const shelf = shelves.find(s => s.id === target.dataset.id);
      if (shelf) {
        const rect = target.getBoundingClientRect();
        const canvasRect = canvasEl.getBoundingClientRect();
        const x = rect.left - canvasRect.left + rect.width / 2;
        const y = rect.bottom - canvasRect.top + 8;
        showShelfTooltip(shelf, x, y, canvasEl);
      }
    } else if (!editorMode && e.button === 0 && !target) {
      hideShelfTooltip();
    }
  });

  // --- Touch Support for Mobile (Pan & Zoom) ---
  let lastTouchDistance = 0;
  let lastTouchCenter = null;

  canvasEl.addEventListener('touchstart', (e) => {
    // 2-finger touch: Zoom
    if (e.touches.length === 2) {
      e.preventDefault(); // Prevent browser zoom
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      lastTouchDistance = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
      lastTouchCenter = {
        x: (t1.clientX + t2.clientX) / 2,
        y: (t1.clientY + t2.clientY) / 2
      };
      return;
    }

    // 1-finger touch: Pan
    if (e.touches.length === 1) {
      // Check if touching a clickable element (shelf/wall)
      const target = e.target.closest('.wall-line, .shelf-group');
      if (editorMode && currentMode === 'select' && target) {
        // Selection/Drag logic for editor (if needed on mobile)
        // For now, prioritize Panning on mobile unless in specific edit mode interactions
        // But usually mobile users expect Pan on canvas drag
      } else {
        isPanning = true;
        panStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      }
    }
  }, { passive: false });

  canvasEl.addEventListener('touchmove', (e) => {
    if (e.touches.length === 2) {
      e.preventDefault();
      const t1 = e.touches[0];
      const t2 = e.touches[1];
      const currentDistance = Math.hypot(t2.clientX - t1.clientX, t2.clientY - t1.clientY);
      const currentCenter = {
        x: (t1.clientX + t2.clientX) / 2,
        y: (t1.clientY + t2.clientY) / 2
      };

      if (lastTouchDistance > 0) {
        const deltaScale = currentDistance / lastTouchDistance;
        const zoomStep = deltaScale;
        // Apply Zoom
        const oldScale = editorScale;
        const newScale = Math.max(0.2, Math.min(5, oldScale * deltaScale)); // Multiplicative zoom feels more natural for pinch

        // Zoom towards center of pinch
        const rect = canvasEl.getBoundingClientRect();
        const mouseX = lastTouchCenter.x - rect.left;
        const mouseY = lastTouchCenter.y - rect.top;

        const worldX = (mouseX - editorPan.x) / oldScale;
        const worldY = (mouseY - editorPan.y) / oldScale;

        editorPan.x = mouseX - worldX * newScale;
        editorPan.y = mouseY - worldY * newScale;
        editorScale = newScale;

        // Also Pan if center moved
        /* Optional: Pan while zooming
        const dx = currentCenter.x - lastTouchCenter.x;
        const dy = currentCenter.y - lastTouchCenter.y;
        editorPan.x += dx;
        editorPan.y += dy;
        */

        renderCanvas();
      }

      lastTouchDistance = currentDistance;
      lastTouchCenter = currentCenter;
      return;
    }

    if (isPanning && e.touches.length === 1) {
      e.preventDefault();
      const dx = e.touches[0].clientX - panStart.x;
      const dy = e.touches[0].clientY - panStart.y;
      editorPan.x += dx;
      editorPan.y += dy;
      panStart = { x: e.touches[0].clientX, y: e.touches[0].clientY };
      renderCanvas();
    }
  }, { passive: false });

  canvasEl.addEventListener('touchend', (e) => {
    isPanning = false;
    lastTouchDistance = 0;
  });



  canvasEl.addEventListener('mousemove', (e) => {
    // Palette Drag Follow
    if (paletteDragItem) {
      paletteDragItem.el.style.left = `${e.clientX + 10}px`;
      paletteDragItem.el.style.top = `${e.clientY + 10}px`;
    }

    if (isPanning) {
      const dx = e.clientX - panStart.x;
      const dy = e.clientY - panStart.y;
      editorPan.x += dx;
      editorPan.y += dy;
      panStart = { x: e.clientX, y: e.clientY };
      renderCanvas();
      return;
    }

    if (isSelectingArea) {
      const pt = getWorldCoordinates(e, canvasEl);
      const x = Math.min(startPoint.x, pt.x);
      const y = Math.min(startPoint.y, pt.y);
      let w = Math.abs(pt.x - startPoint.x);
      let h = Math.abs(pt.y - startPoint.y);
      selectionRect = { x, y, width: w, height: h };
      renderCanvas();
      return;
    }

    if (!editorMode) return;
    const worldPt = getWorldCoordinates(e, canvasEl);
    // Snap to grid
    const snapX = Math.round(worldPt.x / GRID_SIZE) * GRID_SIZE;
    const snapY = Math.round(worldPt.y / GRID_SIZE) * GRID_SIZE;

    const coordEl = document.getElementById('cursor-coords');
    if (coordEl) coordEl.textContent = `${snapX}, ${snapY}`;

    if (isDrawing && activeLine) {
      // Constrain to bounds
      const clampedX = Math.max(0, Math.min(currentLayout.width, snapX));
      const clampedY = Math.max(0, Math.min(currentLayout.height, snapY));

      // Orthogonal: pick dominant axis
      const dx = Math.abs(clampedX - startPoint.x);
      const dy = Math.abs(clampedY - startPoint.y);

      if (dx > dy) {
        activeLine.setAttribute('x2', clampedX);
        activeLine.setAttribute('y2', startPoint.y);
      } else {
        activeLine.setAttribute('x2', startPoint.x);
        activeLine.setAttribute('y2', clampedY);
      }
    }

    if (isDraggingShelf && draggingShelf) {
      updateShelfDrag(e, canvasEl);
    }
  });

  window.addEventListener('mouseup', (e) => {
    if (paletteDragItem) {
      handlePaletteDrop(e, canvasEl);
    }
    if (isPanning) {
      isPanning = false;
      canvasEl.style.cursor = '';
    }
  });

  canvasEl.addEventListener('mouseup', () => {
    if (isSelectingArea) {
      isSelectingArea = false;
      const walls = (currentLayout.walls || []).map((w, i) => ({ type: 'wall', index: i, x1: w.x1, y1: w.y1, x2: w.x2, y2: w.y2 }));
      const sList = shelves.map(s => ({ type: 'shelf', ...s }));
      const all = [...walls, ...sList];

      const selected = all.filter(el => {
        if (el.type === 'wall') {
          return isPointInRect(el.x1, el.y1, selectionRect) || isPointInRect(el.x2, el.y2, selectionRect);
        } else {
          return isPointInRect(el.x, el.y, selectionRect);
        }
      });

      if (selected.length > 0) {
        selectedElement = { type: 'multi', items: selected };
      } else {
        selectedElement = null;
      }

      selectionRect = null;
      renderCanvas();
      renderPropertiesPanel();
      return;
    }

    if (!document.getElementById('editor-toolbar')) return;
    if (isDrawing && activeLine) {
      const x1 = parseFloat(activeLine.getAttribute('x1'));
      const y1 = parseFloat(activeLine.getAttribute('y1'));
      const x2 = parseFloat(activeLine.getAttribute('x2'));
      const y2 = parseFloat(activeLine.getAttribute('y2'));

      if (Math.abs(x1 - x2) > 0 || Math.abs(y1 - y2) > 0) {
        currentLayout.walls = currentLayout.walls || [];
        currentLayout.walls.push({ x1, y1, x2, y2 });
        markLayoutDirty();
      }
      activeLine = null;
      isDrawing = false;
      renderCanvas();
    }
    if (isDraggingShelf) finishShelfDrag();
  });

  canvasEl.addEventListener('contextmenu', (e) => e.preventDefault());
}

// Home page zoom binding
function bindHomeCanvasEvents() {
  const canvasEl = document.getElementById('layout-canvas');
  if (!canvasEl || document.getElementById('editor-toolbar')) return;

  canvasEl.addEventListener('wheel', (e) => {
    e.preventDefault();
    const delta = e.deltaY;
    const zoomStep = 0.1;
    const newScale = editorScale + (delta > 0 ? -zoomStep : zoomStep);
    setZoom(newScale);
  });
}

function isPointInRect(x, y, rect) {
  if (!rect) return false;
  return x >= rect.x && x <= rect.x + rect.width && y >= rect.y && y <= rect.y + rect.height;
}

function isElementInSelection(el, type) {
  if (!selectedElement || selectedElement.type !== 'multi') return false;
  return selectedElement.items.some(item => {
    if (type === 'wall' && item.type === 'wall') {
      return item.x1 === el.x1 && item.y1 === el.y1 && item.x2 === el.x2 && item.y2 === el.y2;
    }
    if (type === 'shelf' && item.type === 'shelf') {
      return item.id === el.id;
    }
    return false;
  });
}


// --- Editor Helpers ---
function createSVGLine(x1, y1, x2, y2, classes) {
  const line = document.createElementNS('http://www.w3.org/2000/svg', 'line');
  line.setAttribute('x1', x1); line.setAttribute('y1', y1);
  line.setAttribute('x2', x2); line.setAttribute('y2', y2);
  // SOLID line, no stroke-dasharray
  classes.forEach(c => line.classList.add(c));
  return line;
}

function getCanvasCoordinates(e, canvasEl) {
  const svgEl = canvasEl.querySelector('svg');
  if (!svgEl) return { x: 0, y: 0 };
  const rect = svgEl.getBoundingClientRect();
  return { x: e.clientX - rect.left, y: e.clientY - rect.top };
}

function getWorldCoordinates(e, canvasEl) {
  const pt = getCanvasCoordinates(e, canvasEl);
  return {
    x: (pt.x - editorPan.x) / editorScale,
    y: (pt.y - editorPan.y) / editorScale
  };
}

function setZoom(scale) {
  editorScale = Math.max(0.2, Math.min(5.0, scale));
  renderCanvas();
  const el = document.getElementById('canvas-status-text');
  if (el) el.textContent = `${Math.round(editorScale * 100)}%`;
}

function selectElement(type, data) {
  if (type === null) {
    selectedElement = null;
  } else {
    selectedElement = { type, ...data };
  }
  renderCanvas();
  renderPropertiesPanel();
}


// --- Layout Management & Dialogs ---
async function selectLayout(layoutId, editorMode) {
  const layout = layouts.find(l => l.id === layoutId);
  if (!layout) return;

  currentLayout = layout;
  const status = document.getElementById('canvas-status-text');
  if (status) status.textContent = '벽 변경은 저장 버튼을 눌러 반영됩니다.';

  if (shelfTypes.length === 0) {
    await loadShelfTypes();
  }

  if (editorMode) {
    const select = document.getElementById('layout-select');
    if (select) select.value = layout.id;
    const delBtn = document.getElementById('layout-delete-btn');
    if (delBtn) delBtn.style.display = 'inline-block';

    selectedElement = null;
    currentMode = 'select';
    updateToolbarUI();
    renderPropertiesPanel();
  }

  await loadShelves(layout.id);

  // Fit layout to canvas at 100%
  fitLayoutToView();

  renderCanvas();
}

function fitLayoutToView() {
  const canvasEl = document.getElementById('layout-canvas');
  if (!canvasEl || !currentLayout) return;

  const containerRect = canvasEl.getBoundingClientRect();
  const layoutWidth = currentLayout.width || 800;
  const layoutHeight = currentLayout.height || 600;

  // Calculate scale to fit the layout in the container with some padding
  const scaleX = (containerRect.width * 0.9) / layoutWidth;
  const scaleY = (containerRect.height * 0.9) / layoutHeight;
  editorScale = Math.min(scaleX, scaleY, 1.0); // Don't scale up beyond 100%

  // Center the layout in the canvas, slightly shifted up
  editorPan.x = (containerRect.width - layoutWidth * editorScale) / 2;
  editorPan.y = (containerRect.height - layoutHeight * editorScale) / 2 - 15;
}

function showEmptyState() {
  const canvas = document.getElementById('layout-canvas');
  if (canvas) canvas.innerHTML = '<div class="muted center-message">배치도를 선택하거나 새로 만드세요.</div>';
}

async function createLayout(name, note) {
  const payload = { name, note, width: 800, height: 600, walls: [] };
  const newLayout = await apiRequest('/serials/layouts', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  await loadLayouts();
  renderLayoutSelect();
  selectLayout(newLayout.id, true);
}

async function updateCurrentLayout() {
  if (!currentLayout) return;
  const payload = { name: currentLayout.name, note: currentLayout.note, walls: currentLayout.walls };
  await apiRequest(`/serials/layouts/${currentLayout.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
  const status = document.getElementById('canvas-status-text');
  if (status) status.textContent = '저장됨';
  alert('배치도가 저장되었습니다.');
}

async function deleteCurrentLayout() {
  if (!currentLayout || !confirm(`'${currentLayout.name}' 삭제하시겠습니까? 배치된 서가가 있으면 삭제할 수 없습니다.`)) return;
  try {
    await apiRequest(`/serials/layouts/${currentLayout.id}`, { method: 'DELETE' });
    currentLayout = null;
    await loadLayouts();
    renderLayoutSelect();

    if (layouts.length > 0) selectLayout(layouts[0].id, true);
    else showEmptyState();
  } catch (err) {
    alert(err.message || '배치도를 삭제할 수 없습니다.');
  }
}


// --- Event Bindings ---
function bindToolbarEvents() {
  document.querySelectorAll('.tool-btn[data-mode]').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.tool-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      currentMode = btn.dataset.mode;
      updateToolbarUI();
    });
  });

  document.getElementById('save-layout-btn')?.addEventListener('click', updateCurrentLayout);
  document.getElementById('zoom-reset')?.addEventListener('click', () => {
    fitLayoutToView();
    renderCanvas();
    const el = document.getElementById('canvas-status-text');
    // Display 0.9 scale as 100%
    if (el) el.textContent = `${Math.round((editorScale / 0.9) * 100)}%`;
  });

  document.getElementById('layout-create-btn')?.addEventListener('click', () => {
    const d = document.getElementById('layout-meta-dialog');
    if (d) { d.style.display = 'block'; d.showModal(); }
  });
  document.getElementById('layout-delete-btn')?.addEventListener('click', deleteCurrentLayout);
  document.getElementById('manage-types-btn')?.addEventListener('click', () => {
    const d = document.getElementById('shelf-type-dialog');
    if (d) { d.style.display = 'block'; d.showModal(); renderShelfTypeList(); }
  });
}

function updateToolbarUI() { }

function bindDialogEvents() {
  const closeBtns = document.querySelectorAll('[data-action="close"]');
  closeBtns.forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      const d = e.target.closest('dialog');
      d.close();
      d.style.display = 'none';
    });
  });

  const metaForm = document.getElementById('layout-meta-form');
  if (metaForm) metaForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const data = new FormData(e.target);
    await createLayout(data.get('name'), data.get('note'));
    const d = document.getElementById('layout-meta-dialog');
    d.close(); d.style.display = 'none';
    e.target.reset();
  });

  const typeForm = document.getElementById('shelf-type-form');
  if (typeForm) typeForm.addEventListener('submit', async (e) => {
    e.preventDefault();
    const form = e.target;
    const id = form.querySelector('[name="id"]')?.value;
    const payload = {
      name: form.querySelector('[name="name"]').value,
      rows: parseInt(form.querySelector('[name="rows"]').value),
      columns: parseInt(form.querySelector('[name="columns"]').value),
      width: parseInt(form.querySelector('[name="columns"]').value) * UNIT_SIZE,
      height: parseInt(form.querySelector('[name="rows"]').value) * UNIT_SIZE * 0.6
    };

    const url = id ? `/serials/shelf-types/${id}` : '/serials/shelf-types';
    const method = id ? 'PUT' : 'POST';
    await apiRequest(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });

    await loadShelfTypes();
    renderShelfPalette();
    renderShelfTypeList();
    resetShelfTypeForm();
  });

  // 새로 등록 버튼 - 폼 초기화
  document.getElementById('shelf-type-new')?.addEventListener('click', resetShelfTypeForm);
}

function resetShelfTypeForm() {
  const form = document.getElementById('shelf-type-form');
  if (!form) return;
  form.reset();
  form.querySelector('[name="id"]').value = '';

  const list = document.getElementById('shelf-type-list');
  if (list) list.querySelectorAll('.list-item').forEach(i => i.classList.remove('selected'));

  const title = document.getElementById('shelf-type-dialog-title');
  if (title) title.textContent = '서가 타입 관리';
}

function renderShelfTypeList() {
  const list = document.getElementById('shelf-type-list');
  if (!list) return;

  list.innerHTML = shelfTypes.map((t, idx) => {
    const color = getShelfTypeColor(idx, t);
    return `
      <div class="list-item shelf-type-item" data-id="${escapeHtml(t.id)}">
        <span class="shelf-type-color" style="background:${escapeHtml(color)}"></span>
        <span class="shelf-type-info">${escapeHtml(t.name)} (${escapeHtml(t.rows)}행 × ${escapeHtml(t.columns)}칸)</span>
        <button class="btn-icon delete-type" data-id="${escapeHtml(t.id)}">🗑️</button>
      </div>`;
  }).join('');

  // 항목 클릭 시 폼에 정보 로드 (수정 모드)
  list.querySelectorAll('.shelf-type-item').forEach(item => {
    item.addEventListener('click', (e) => {
      if (e.target.classList.contains('delete-type')) return; // 삭제 버튼 클릭 무시

      const typeId = item.dataset.id;
      const shelfType = shelfTypes.find(t => t.id === typeId);
      if (!shelfType) return;

      // 폼에 정보 채우기
      const form = document.getElementById('shelf-type-form');
      if (!form) return;

      form.querySelector('[name="id"]').value = shelfType.id;
      form.querySelector('[name="name"]').value = shelfType.name;
      form.querySelector('[name="rows"]').value = shelfType.rows;
      form.querySelector('[name="columns"]').value = shelfType.columns;

      // 선택 상태 표시
      list.querySelectorAll('.list-item').forEach(i => i.classList.remove('selected'));
      item.classList.add('selected');

      // 다이얼로그 제목 변경
      const title = document.getElementById('shelf-type-dialog-title');
      if (title) title.textContent = '서가 타입 수정';
    });
  });

  list.querySelectorAll('.delete-type').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const typeId = btn.dataset.id;

      if (!confirm('이 서가 타입을 삭제하시겠습니까? 사용 중인 서가 타입은 삭제할 수 없습니다.')) return;

      try {
        await apiRequest(`/serials/shelf-types/${typeId}`, { method: 'DELETE' });
        await loadShelfTypes();
        renderShelfPalette();
        renderShelfTypeList();
        renderCanvas();
      } catch (err) {
        alert(err.message || '서가 타입을 삭제할 수 없습니다.');
      }
    });
  });
}

function bindSidebarEvents() {
  document.getElementById('layout-select')?.addEventListener('change', (e) => selectLayout(e.target.value, true));
}

function renderLayoutSelect() {
  const select = document.getElementById('layout-select');
  if (select) select.innerHTML = layouts.map(l => `<option value="${escapeHtml(l.id)}">${escapeHtml(l.name)}</option>`).join('');
}

function renderPropertiesPanel() {
  const panel = document.getElementById('properties-content');
  if (!panel) return;

  if (!selectedElement) {
    panel.innerHTML = '<div class="muted center-message">선택된 요소가 없습니다.</div>';
    return;
  }

  if (selectedElement.type === 'multi') {
    panel.innerHTML = `
          <div class="stack tight">
            <div>${selectedElement.items.length}개 요소 선택됨</div>
            <button class="btn danger small" id="prop-multi-delete">전체 삭제</button>
          </div>
        `;
    document.getElementById('prop-multi-delete').addEventListener('click', async () => {
      if (!confirm('선택한 요소를 모두 삭제하시겠습니까?')) return;

      const wallIndices = selectedElement.items.filter(i => i.type === 'wall').map(i => i.index).sort((a, b) => b - a);
      const shelfIds = selectedElement.items.filter(i => i.type === 'shelf').map(i => i.id);

      try {
        for (const sid of shelfIds) {
          await apiRequest(`/serials/shelves/${sid}`, { method: 'DELETE' });
        }
      } catch (err) {
        alert(err.message || '선택한 서가를 삭제할 수 없습니다.');
        return;
      }

      wallIndices.forEach(idx => {
        currentLayout.walls.splice(idx, 1);
      });
      if (wallIndices.length) markLayoutDirty();

      shelves = shelves.filter(s => !shelfIds.includes(s.id));
      selectedElement = null;
      renderCanvas();
      renderPropertiesPanel();
    });
    return;
  }

  if (selectedElement.type === 'shelf') {
    const shelf = selectedElement;
    panel.innerHTML = `
          <div class="form-row"><label>명칭</label><input id="prop-code" value="${escapeHtml(shelf.code)}"></div>
          <div class="form-row"><label>X</label><input id="prop-x" type="number" value="${escapeHtml(shelf.x)}"></div>
          <div class="form-row"><label>Y</label><input id="prop-y" type="number" value="${escapeHtml(shelf.y)}"></div>
          <div class="form-row"><label>회전°</label><input id="prop-rot" type="number" value="${escapeHtml(shelf.rotation || 0)}"></div>
          <div class="stack tight" style="margin-top:10px">
            <button class="btn primary small" id="prop-update">수정</button>
            <button class="btn danger small" id="prop-delete">삭제</button>
          </div>
        `;
    document.getElementById('prop-update')?.addEventListener('click', async () => {
      const updates = {
        code: document.getElementById('prop-code').value,
        x: parseInt(document.getElementById('prop-x').value),
        y: parseInt(document.getElementById('prop-y').value),
        rotation: parseInt(document.getElementById('prop-rot').value) || 0
      };
      await apiRequest(`/serials/shelves/${shelf.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(updates) });
      Object.assign(shelf, updates);
      const idx = shelves.findIndex(s => s.id === shelf.id);
      if (idx >= 0) Object.assign(shelves[idx], updates);
      renderCanvas();
    });
    document.getElementById('prop-delete')?.addEventListener('click', async () => {
      if (confirm('삭제?')) {
        try {
          await apiRequest(`/serials/shelves/${shelf.id}`, { method: 'DELETE' });
          shelves = shelves.filter(s => s.id !== shelf.id);
          selectElement(null);
        } catch (err) {
          alert(err.message || '서가를 삭제할 수 없습니다.');
        }
      }
    });
  } else if (selectedElement.type === 'wall') {
    const idx = selectedElement.index;
    panel.innerHTML = `<div class="stack tight"><button class="btn danger small" id="prop-wall-delete">벽 삭제</button></div>`;
    document.getElementById('prop-wall-delete')?.addEventListener('click', () => {
      currentLayout.walls.splice(idx, 1);
      markLayoutDirty();
      selectElement(null);
    });
  }
}

// --- Drag Helpers ---
function startShelfDrag(e, shelf, canvasEl) {
  isDraggingShelf = true;
  draggingShelf = shelf;
  const pt = getWorldCoordinates(e, canvasEl);
  dragOffset.x = pt.x - shelf.x;
  dragOffset.y = pt.y - shelf.y;
}

function updateShelfDrag(e, canvasEl) {
  if (!draggingShelf) return;
  const pt = getWorldCoordinates(e, canvasEl);
  const snapX = Math.round((pt.x - dragOffset.x) / GRID_SIZE) * GRID_SIZE;
  const snapY = Math.round((pt.y - dragOffset.y) / GRID_SIZE) * GRID_SIZE;

  const g = document.querySelector(`.shelf-group[data-id="${draggingShelf.id}"]`);
  if (g) g.setAttribute('transform', `translate(${snapX}, ${snapY}) rotate(${draggingShelf.rotation || 0})`);

  draggingShelf._tempX = snapX;
  draggingShelf._tempY = snapY;
}

async function finishShelfDrag() {
  isDraggingShelf = false;
  if (draggingShelf && draggingShelf._tempX !== undefined) {
    const x = draggingShelf._tempX;
    const y = draggingShelf._tempY;
    draggingShelf.x = x; draggingShelf.y = y;
    delete draggingShelf._tempX; delete draggingShelf._tempY;

    await apiRequest(`/serials/shelves/${draggingShelf.id}`, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ x, y }) });
    renderCanvas();
  }
  draggingShelf = null;
}

async function handlePaletteDrop(e, canvasEl) {
  if (!paletteDragItem || !currentLayout) {
    if (paletteDragItem) { paletteDragItem.el.remove(); paletteDragItem = null; }
    return;
  }

  const rect = canvasEl.getBoundingClientRect();
  const isOverCanvas = e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom;

  if (isOverCanvas) {
    const pt = getWorldCoordinates(e, canvasEl);
    const x = Math.round(pt.x / GRID_SIZE) * GRID_SIZE;
    const y = Math.round(pt.y / GRID_SIZE) * GRID_SIZE;

    const code = prompt(`'${paletteDragItem.name}' 배치:\n서가 관리번호/명칭을 입력하세요:`, `S-${shelves.length + 1}`);

    if (code) {
      const payload = {
        layout_id: currentLayout.id,
        shelf_type_id: paletteDragItem.typeId,
        code: code,
        x, y,
        rotation: 0
      };
      try {
        const newShelf = await apiRequest('/serials/shelves', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
        shelves.push(newShelf);
        renderCanvas();
        selectElement('shelf', newShelf);
      } catch (err) {
        console.error(err);
        alert('배치 실패: ' + err.message);
      }
    }
  }

  paletteDragItem.el.remove();
  paletteDragItem = null;
}


// --- Palette ---
// renderShelfTypeList (기존 코드 수정)
// ... (이 부분은 위에서 찾은 renderShelfTypeList 내부 수정)

// --- Palette ---
function renderShelfPalette() {
  const container = document.getElementById('shelf-palette');
  if (!container) return;
  container.innerHTML = '';

  shelfTypes.forEach((t, idx) => {
    const el = document.createElement('div');
    el.className = 'palette-item';

    // 서가 타입 색상 적용
    const color = getShelfTypeColor(idx, t);
    const borderColor = color;
    const bgColor = color; // Solid color

    el.style.borderColor = borderColor;
    el.style.backgroundColor = bgColor;
    el.style.color = '#ffffff'; // White text
    el.innerHTML = `<div class="palette-label">${escapeHtml(t.name)}</div>`;

    el.addEventListener('mousedown', (e) => {
      e.preventDefault();
      if (e.button !== 0) return;

      const ghost = document.createElement('div');
      ghost.className = 'palette-ghost';
      ghost.style.cssText = `
                 position: fixed;
                 left: ${e.clientX}px;
                 top: ${e.clientY}px;
                 width: 50px;
                 height: 25px;
                 background: rgba(59, 130, 246, 0.5);
                 border: 1px solid #3b82f6;
                 pointer-events: none;
                 z-index: 9999;
                 font-size: 9px;
                 display: flex;
                 align-items: center;
                 justify-content: center;
                 color: white;
                 border-radius: 2px;
             `;
      ghost.textContent = t.name;

      document.body.appendChild(ghost);

      paletteDragItem = {
        typeId: t.id,
        name: t.name,
        el: ghost
      };
    });

    container.appendChild(el);
  });
}

(function () {
  // ====== Selectores reales de Actais (verificados con HTML del 18-may-2026) ======
  const SELECTOR_BODY = '.main-page-body';
  const SELECTOR_CALENDAR_CONTAINER = '#workerCalendarTotalContainer, #MonthTable';
  const SELECTOR_CALENDAR_CELL = '.month-calendar-cell';

  // Mapeo S_X → código interno Shiftia. S_X confirmados con HTML real del
  // calendario de Actais (Hospital de Jove). El resto son hipótesis basadas
  // en el PDF de planificación anual; ajustables conforme aparezcan.
  const SHIFT_CODE_MAP = {
    'S_1':  { code: 'M',   label: 'Mañana', reduced: false },
    'S_10': { code: 'D',   label: 'Descanso' },
    'S_30': { code: 'T',   label: 'Tarde' },
    'S_34': { code: 'N',   label: 'Noche' } // confirmado con HTML real 12-jun-2026
    // Hipótesis pendientes de confirmar con HTML real:
    // 'S_X': { code: 'MT',  label: 'Mañana-Tarde 12h' }, // diálisis (confirmar S_ de una celda real)
    // 'S_X': { code: 'N',   label: 'Noche' },
    // 'S_X': { code: 'M7H', label: 'Mañana 7H', reduced: true },
    // 'S_X': { code: 'M8',  label: 'Mañana 8H' },
    // 'S_X': { code: 'VAC', label: 'Vacaciones' },
    // 'S_X': { code: 'BAJ', label: 'Baja' },
    // 'S_X': { code: 'SP',  label: 'Sin Planificar' },
    // 'S_X': { code: 'CJ',  label: 'Cómputo Jornada' },
    // 'S_X': { code: 'FOR', label: 'Formación' },
    // 'S_X': { code: 'HS',  label: 'Horas Sindicales' }
  };

  // Catálogo completo de códigos válidos (espejado de shiftia-director).
  // Sirve como referencia para validación + UI.
  const VALID_SHIFTS = {
    M:    { label: 'Mañana', category: 'work', hours: null },
    MT:   { label: 'Mañana-Tarde 07:00–19:00 (12h)', category: 'work', hours: 11.5 },
    M7H:  { label: 'Mañana 07:00–14:00', category: 'work', hours: 7, reduced: true },
    M8:   { label: 'Mañana 07:00–15:00', category: 'work', hours: 8 },
    M4H:  { label: 'Mañana 08:00–12:00', category: 'work', hours: 4, reduced: true },
    M6:   { label: 'Mañana 08:00–14:00', category: 'work', hours: 6, reduced: true },
    M55:  { label: 'Mañana 5,5h', category: 'work', hours: 5.5, reduced: true },
    M6R:  { label: 'Mañana 6h reducida', category: 'work', hours: 6, reduced: true },
    MR:   { label: 'Mañana reducida', category: 'work', reduced: true },
    T:    { label: 'Tarde 15:00–22:00', category: 'work', hours: 7 },
    N:    { label: 'Noche 22:00–08:00', category: 'work', hours: 10 },
    D:    { label: 'Descanso', category: 'rest' },
    L:    { label: 'Libre', category: 'rest' },
    LD:   { label: 'Libre Disposición', category: 'rest' },
    FN:   { label: 'Festivo Nacional', category: 'rest' },
    SP:   { label: 'Sin Planificar', category: 'unplanned', color: 'gray' },
    VAC:  { label: 'Vacaciones', category: 'absence' },
    VAN:  { label: 'Vacaciones arrastradas', category: 'absence' },
    VAA:  { label: 'Vacaciones Año Anterior', category: 'absence' },
    BAJ:  { label: 'Baja', category: 'absence' },
    LAC:  { label: 'Lactancia', category: 'absence' },
    AE:   { label: 'Asuntos propios', category: 'absence' },
    EX:   { label: 'Excedencia', category: 'absence' },
    PM:   { label: 'Permiso', category: 'absence' },
    MTC:  { label: 'Motivo familiar', category: 'absence' },
    CJ:   { label: 'Cómputo de Jornada', category: 'compensatory', hours: 7 },
    CAA:  { label: 'Cómputo Año Anterior', category: 'compensatory' },
    DLA:  { label: 'Días Libre Disp. Año Anterior', category: 'compensatory' },
    FOR:  { label: 'Formación', category: 'training', hours: 7 },
    HS:   { label: 'Horas Sindicales', category: 'union' },
    HF:   { label: 'Horas Festivas', category: 'work' },
    INT:  { label: 'Intervención', category: 'work' },
    IQF:  { label: 'IQF', category: 'work' },
    G17:  { label: 'Guardia 17h', category: 'guard', suffix: true },
    G24:  { label: 'Guardia 24h', category: 'guard', suffix: true }
  };

  // Fallback por texto en .schedule (cuando no hay clase S_X conocida)
  const SHIFT_TEXT_MAP = [
    // Diálisis: turno largo 07:00–19:00 (12h). Va PRIMERO para ganar a
    // /mañana/ y /tarde/ (cubre ambas franjas). "MT" en el PDF de la unidad.
    { match: /07[:.h]?00.*19[:.h]?00/, code: 'MT' },
    { match: /ma[ñn]ana.*tarde/i,      code: 'MT' },
    { match: /noche/i,          code: 'N' },
    { match: /tarde/i,          code: 'T' },
    { match: /mañana.*07:00.*14:00/i, code: 'M7H' },
    { match: /mañana.*07:00.*15:00/i, code: 'M8' },
    { match: /mañana.*08:00.*12:00/i, code: 'M4H' },
    { match: /mañana.*08:00.*14:00/i, code: 'M6' },
    { match: /mañana.*08:00.*15:00/i, code: 'M8' },
    { match: /mañana/i,         code: 'M' },
    { match: /descans/i,        code: 'D' },
    { match: /vacac/i,          code: 'VAC' },
    { match: /libre.*dispos/i,  code: 'LD' },
    { match: /libre/i,          code: 'L' },
    { match: /baja/i,           code: 'BAJ' },
    { match: /lactanc/i,        code: 'LAC' },
    { match: /formaci/i,        code: 'FOR' },
    { match: /sindical/i,       code: 'HS' },
    { match: /computo|cómputo/i, code: 'CJ' },
    { match: /sin planif/i,     code: 'SP' }
  ];

  let lastContextSig = null;
  let menuEl = null;

  // ====== Trabajador seleccionado según el ESTADO INTERNO de Actais ======
  // page-bridge.js (mundo de la página) lee jQuery(tree).tree('getSelected')
  // y lo publica por postMessage. Es la fuente de verdad: id real (w_46) +
  // nombre oficial. Las heurísticas de DOM quedan solo como fallback.
  let bridgeWorker = null; // { id: '46', name: 'APELLIDOS, NOMBRE' } | null

  window.addEventListener('message', (ev) => {
    if (ev.source !== window) return;
    const d = ev.data;
    if (d?.source !== 'shiftia-bridge' || d.type !== 'selectedWorker') return;
    const shared = (typeof self !== 'undefined' && self.ShiftiaShared) || {};
    bridgeWorker = shared.normalizeBridgeWorker ? shared.normalizeBridgeWorker(d.payload) : null;
    broadcastContext();
  });

  // ====== Parser de celda ======
  function parseCellElement(cellEl) {
    const idAttr = cellEl.id || '';
    // Formato esperado: "1122_27-04-2026"
    const m = idAttr.match(/^(\d+)_(\d{2})-(\d{2})-(\d{4})$/);
    let workerId = null, day = null, month = null, year = null;
    if (m) {
      workerId = m[1];
      // SARA almacena scheduleData[year-month][wId] como array 0-indexed (día 1 -> [0]).
      // Actais codifica el día como 1-31. Convertimos a 0-based aquí.
      day = parseInt(m[2], 10) - 1;
      month = parseInt(m[3], 10) - 1;
      year = parseInt(m[4], 10);
    }

    // Tipo de turno: primero por clase S_X, luego por texto .schedule.
    let shift = null, shiftLabel = null;
    const sClass = Array.from(cellEl.classList).find(c => /^S_\d+$/.test(c));
    if (sClass && SHIFT_CODE_MAP[sClass]) {
      shift = SHIFT_CODE_MAP[sClass].code;
      shiftLabel = SHIFT_CODE_MAP[sClass].label;
    } else {
      const scheduleText = cellEl.querySelector('.schedule')?.textContent?.trim() || '';
      shiftLabel = scheduleText;
      for (const m of SHIFT_TEXT_MAP) {
        if (m.match.test(scheduleText)) { shift = m.code; break; }
      }
      // 3er nivel: COLOR de fondo de la celda (paleta confirmada de Actais).
      // Cubre clases S_X aún no mapeadas y textos no reconocibles.
      if (!shift) {
        const colorShared = (typeof self !== 'undefined' && self.ShiftiaShared) || {};
        if (colorShared.shiftFromColor) {
          shift = colorShared.shiftFromColor(cellEl.style?.backgroundColor || '');
          if (shift && !shiftLabel) shiftLabel = shift + ' (por color)';
        }
      }
    }

    // AUSENCIA: Actais la pinta dentro de la celda como
    // <div class="slot ProgInc absence … Inc_NNNNN VAC"> en .p3.
    // El código viene como CLASE (los días de continuación no llevan texto).
    // Verificado con HTML real del 12-jun-2026. Pisa el turno base.
    const slotEl = cellEl.querySelector('.p3 .absence, .p3 .slot, .absence.slot');
    const shared = (typeof self !== 'undefined' && self.ShiftiaShared) || {};
    if (slotEl && shared.absenceCodeFromCell) {
      const code = shared.absenceCodeFromCell({
        classes: Array.from(slotEl.classList),
        text: slotEl.textContent || ''
      });
      if (code) {
        shift = code;
        shiftLabel = (VALID_SHIFTS[code]?.label || code) + ' (sobre ' + (shiftLabel || '?') + ')';
      }
    }

    const scheduleFull = cellEl.querySelector('.schedule')?.textContent?.trim() || '';
    const planification = cellEl.querySelector('.planification')?.textContent?.trim() || '';
    const punchText = cellEl.querySelector('.p2 .move')?.textContent?.trim() || '';
    const idProgrammed = cellEl.getAttribute('idprogrammedcalendar') || null;

    return {
      workerId, day, month, year,
      shift, shiftLabel, scheduleFull, planification, punchText,
      idProgrammedCalendar: idProgrammed,
      cellId: idAttr
    };
  }

  function detectWorkerName() {
    // 0. Fuente de verdad: estado interno del árbol de Actais (vía bridge).
    if (bridgeWorker?.name) return bridgeWorker.name;
    return detectWorkerNameFromDom();
  }

  function detectWorkerNameFromDom() {
    // Fallback heurístico. La validación de formato "APELLIDOS, NOMBRE" vive
    // en ShiftiaShared.pickWorkerName (testeada): descarta basura tipo
    // "SANITARIO HEMATOLOGIA DUE …" del árbol.
    const candidates = [];
    const push = (el) => { if (el) candidates.push(el.textContent || ''); };

    // 1. Nodo SELECCIONADO en el árbol de empleados (visor de gestión).
    //    Actais usa easyui-tree: el nodo activo lleva .tree-node-selected y el
    //    nombre va en su .tree-title (verificado con HTML real 12-jun-2026).
    document.querySelectorAll(
      '.tree-node-selected .tree-title, ' +
      '.tree-node-selected, ' +
      '.jstree-clicked, ' +
      '[aria-selected="true"], ' +
      '.dx-treeview-node.dx-state-selected .dx-treeview-item, ' +
      'li.ui-state-active, .ui-state-active'
    ).forEach(push);

    // 2. Cabecera del modal "Calendario del empleado".
    document.querySelectorAll(
      '#workerCalendarTotalContainer .modal-title, ' +
      '#workerCalendarTotalContainer h3, ' +
      '#workerCalendarTotalContainer h4, ' +
      '.modal-header .modal-title'
    ).forEach(push);

    // 3. Selectores legacy de nombre.
    document.querySelectorAll(
      '[id*="lblWorkerName"], [id*="WorkerName"], .worker-name-header, .employee-name'
    ).forEach(push);

    // 4. Cabecera "Bienvenido, …" (en "Mi calendario" el worker es la propia
    //    usuaria). Va al final: en el visor de gestión sería la supervisora,
    //    no el trabajador seleccionado.
    const shellHeader = document.querySelector('#welcome-msg, #lblWelcome, .welcome-message');
    if (shellHeader) {
      const m = shellHeader.textContent.match(/Bienvenido,?\s*(.+?)(?:\s*\(|$)/);
      if (m) candidates.push(m[1]);
    }

    // 5. Título de la pestaña.
    candidates.push(document.title || '');

    const shared = (typeof self !== 'undefined' && self.ShiftiaShared) || {};
    return shared.pickWorkerName ? shared.pickWorkerName(candidates) : null;
  }

  function detectModule() {
    const menu = document.querySelector('#cssmenu .selected, #cssmenu .active');
    return menu?.textContent?.trim() || document.title || null;
  }

  function readContext() {
    return {
      url: location.href,
      module: detectModule(),
      worker: detectWorkerName(),
      hasCalendar: !!document.querySelector(SELECTOR_CALENDAR_CONTAINER),
      cellsCount: document.querySelectorAll(SELECTOR_CALENDAR_CELL).length
    };
  }

  function broadcastContext() {
    const ctx = readContext();
    const sig = JSON.stringify(ctx);
    if (sig === lastContextSig) return;
    lastContextSig = sig;
    chrome.runtime.sendMessage({ type: 'actais:context', payload: ctx }).catch(() => {});
  }

  // ====== Scrape del mes visible ======
  // Lee TODAS las celdas del calendario abierto (excluyendo .other-month),
  // las parsea con parseCellElement (que ya resuelve turnos S_X y ausencias
  // VAC/BAJ/… por la clase del .slot.absence) y delega el ensamblado en
  // ShiftiaShared.assembleMonth (shared/month-assembler.js, testeado en Node).
  function scrapeVisibleMonth() {
    const container = document.querySelector(SELECTOR_CALENDAR_CONTAINER);
    if (!container) {
      return { ok: false, error: 'No hay calendario visible en Actais. Abre la planilla de un trabajador.' };
    }
    const cellEls = Array.from(container.querySelectorAll(SELECTOR_CALENDAR_CELL))
      .filter(el => !el.classList.contains('other-month'));
    const shared = (typeof self !== 'undefined' && self.ShiftiaShared) || null;
    if (!shared?.assembleMonth) {
      return { ok: false, error: 'month-assembler no cargado (recarga la extensión)' };
    }
    const assembled = shared.assembleMonth(cellEls.map(parseCellElement));
    if (!assembled.ok) return assembled;

    const absenceSet = new Set(shared.ABSENCE_CODES || []);
    assembled.stats.absenceDays = assembled.cells.filter(c => absenceSet.has(c)).length;

    // Nombre: solo se usa el del bridge si su id CASA con el de las celdas
    // escaneadas (garantiza que nombre y planilla son del mismo trabajador).
    // Si el árbol apunta a otra persona (calendario aún sin recargar), se cae
    // al fallback de DOM, y en última instancia el backend resuelve por
    // actaisId vinculado.
    if (bridgeWorker?.name && (!bridgeWorker.id || String(bridgeWorker.id) === String(assembled.workerId))) {
      assembled.workerName = bridgeWorker.name;
    } else {
      assembled.workerName = detectWorkerNameFromDom();
    }
    return assembled;
  }

  // El sidepanel (vía service worker) pide el scrape bajo demanda.
  chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
    if (msg?.type === 'actais:scrapeMonth') {
      sendResponse(scrapeVisibleMonth());
    }
    return false; // respuesta síncrona
  });

  // ====== Menú contextual flotante ======
  const MENU_ACTIONS = [
    { id: 'librar',           label: '🆓 Librar este día', group: 'ai' },
    { id: 'whoCovers',        label: '👥 ¿Quién cubre?', group: 'ai' },
    { id: 'vacaciones',       label: '🏖️ Marcar vacaciones', group: 'ai' },
    { id: 'cambio',           label: '🔁 Proponer cambio', group: 'ai' },
    { id: 'validateConvenio', label: '⚖️ Validar convenio', group: 'ai' },
    { id: 'alternativas',     label: '🧠 Alternativas IA', group: 'ai' },
    { id: 'syncCellChange',   label: '📥 Volcar cambio sin IA a Shiftia', group: 'sync' }
  ];

  function closeMenu() {
    if (menuEl) { menuEl.remove(); menuEl = null; }
  }

  function openMenu(x, y, cell) {
    closeMenu();
    menuEl = document.createElement('div');
    menuEl.className = 'shiftia-ctx-menu';
    menuEl.style.left = `${x}px`;
    menuEl.style.top = `${y}px`;

    const dateStr = (cell.day != null && cell.month != null && cell.year)
      ? `${String(cell.day + 1).padStart(2, '0')}/${String(cell.month + 1).padStart(2, '0')}/${cell.year}`
      : 'fecha ?';

    const header = document.createElement('div');
    header.className = 'shiftia-ctx-header';
    header.textContent = `Worker ${cell.workerId || '?'} · ${dateStr} · ${cell.shift || cell.shiftLabel || '—'}`;
    menuEl.appendChild(header);

    if (cell.planification) {
      const sub = document.createElement('div');
      sub.className = 'shiftia-ctx-sub';
      sub.textContent = cell.planification;
      menuEl.appendChild(sub);
    }

    // Acciones IA (deterministas, sobre la planilla interna)
    MENU_ACTIONS.filter(a => a.group === 'ai').forEach((act) => {
      const btn = document.createElement('button');
      btn.className = 'shiftia-ctx-btn';
      btn.textContent = act.label;
      btn.addEventListener('click', () => runAction(act.id, cell));
      menuEl.appendChild(btn);
    });

    // Separador visual + acción de sincronización sin IA
    const sep = document.createElement('div');
    sep.className = 'shiftia-ctx-sep';
    sep.textContent = 'Sincronización';
    menuEl.appendChild(sep);

    MENU_ACTIONS.filter(a => a.group === 'sync').forEach((act) => {
      const btn = document.createElement('button');
      btn.className = 'shiftia-ctx-btn shiftia-ctx-btn-sync';
      btn.textContent = act.label;
      btn.addEventListener('click', () => runAction(act.id, cell));
      menuEl.appendChild(btn);
    });

    const close = document.createElement('button');
    close.className = 'shiftia-ctx-close';
    close.textContent = '×';
    close.setAttribute('aria-label', 'Cerrar menú');
    close.addEventListener('click', closeMenu);
    menuEl.appendChild(close);

    const footer = document.createElement('div');
    footer.className = 'shiftia-ctx-footer';
    footer.innerHTML = 'vibecoded by <a href="https://highkeylabs.es" target="_blank" rel="noopener">Highkey Labs</a>';
    menuEl.appendChild(footer);

    document.body.appendChild(menuEl);

    // Reposicionar si se sale del viewport
    const rect = menuEl.getBoundingClientRect();
    if (rect.right > window.innerWidth) menuEl.style.left = `${window.innerWidth - rect.width - 8}px`;
    if (rect.bottom > window.innerHeight) menuEl.style.top = `${window.innerHeight - rect.height - 8}px`;
  }

  async function runAction(actionId, cell) {
    let result = menuEl?.querySelector('.shiftia-ctx-result');
    if (!result) {
      result = document.createElement('div');
      result.className = 'shiftia-ctx-result';
      menuEl?.appendChild(result);
    }
    result.textContent = 'Consultando…';
    const res = await chrome.runtime.sendMessage({
      type: 'shiftia:askEngine',
      payload: { action: actionId, args: cell }
    }).catch((e) => ({ ok: false, error: e.message }));
    if (!res?.ok) {
      result.innerHTML = `<span class="shiftia-ctx-err">${escapeHtml(res?.error || 'Error inesperado')}</span>`;
      return;
    }
    result.innerHTML = formatResult(res.data);
  }

  function formatResult(data) {
    // Render visual (veredicto, reglas, candidatos) — shared/result-formatter.js
    const shared = (typeof self !== 'undefined' && self.ShiftiaShared) || {};
    if (shared.formatAssistantResult) return shared.formatAssistantResult(data);
    // Fallback mínimo si el formatter no cargó
    if (data == null) return '<em>Sin datos</em>';
    if (typeof data === 'string') return escapeHtml(data);
    return '<pre>' + escapeHtml(JSON.stringify(data, null, 2)) + '</pre>';
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // ====== Wiring ======
  function init() {
    const body = document.querySelector(SELECTOR_BODY) || document.body;
    if (!body) { setTimeout(init, 500); return; }

    const observer = new MutationObserver(() => {
      clearTimeout(window.__shiftiaDebounce);
      window.__shiftiaDebounce = setTimeout(broadcastContext, 250);
    });
    observer.observe(body, { childList: true, subtree: true, characterData: true });

    // Delegated click sobre celdas reales del calendario de Actais.
    // Alt+click para no interferir con la selección/edición nativa.
    document.addEventListener('click', (ev) => {
      const cellEl = ev.target.closest(SELECTOR_CALENDAR_CELL);
      if (!cellEl) return;
      if (!ev.altKey) return;
      ev.preventDefault();
      ev.stopPropagation();
      const cell = parseCellElement(cellEl);
      cell.worker = detectWorkerName();
      openMenu(ev.pageX, ev.pageY, cell);
    }, true);

    document.addEventListener('click', (ev) => {
      if (menuEl && !menuEl.contains(ev.target) && !ev.target.closest(SELECTOR_CALENDAR_CELL)) {
        closeMenu();
      }
    });
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') closeMenu();
    });

    broadcastContext();
    injectPageBridge();
  }

  function injectPageBridge() {
    const url = chrome.runtime.getURL('content/page-bridge.js');
    const script = document.createElement('script');
    script.src = url;
    script.onload = () => script.remove();
    (document.head || document.documentElement).appendChild(script);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();

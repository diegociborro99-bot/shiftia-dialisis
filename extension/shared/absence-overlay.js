// ============================================================================
// absence-overlay.js — lógica pura verificada contra el HTML real de Actais:
//
// 1. absenceCodeFromCell({classes, text}): las ausencias (VAC, BAJ…) se
//    pintan DENTRO de cada celda como <div class="slot ProgInc absence …
//    Inc_91933 VAC">. El código viene como CLASE exacta; el primer día de la
//    ausencia además lleva el código como texto, los días de continuación no.
//
// 2. pickWorkerName(candidates): valida formato "APELLIDOS, NOMBRE" y
//    descarta la basura del árbol de empleados (departamentos, cadenas largas).
//
// UMD: window.ShiftiaShared en el navegador, module.exports en Node (tests).
// ============================================================================
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.ShiftiaShared = Object.assign(root.ShiftiaShared || {}, factory());
  }
})(typeof self !== 'undefined' ? self : this, function () {

  // Códigos de ausencia/incidencia que Actais pone como clase del slot.
  const ABSENCE_CODES = ['VAC', 'VAN', 'VAA', 'BAJ', 'LAC', 'FOR', 'CJ', 'CAA',
    'DLA', 'HS', 'AE', 'EX', 'PM', 'MTC', 'IT', 'LD'];
  const ABSENCE_SET = new Set(ABSENCE_CODES);

  function absenceCodeFromCell(slot) {
    if (!slot) return null;
    // 1. Por clase exacta (funciona también en días de continuación sin texto)
    for (const c of (slot.classes || [])) {
      if (ABSENCE_SET.has(c)) return c;
    }
    // 2. Fallback por texto ("VAC" en el primer día del tramo)
    const text = (slot.text || '').trim().toUpperCase();
    if (ABSENCE_SET.has(text)) return text;
    return null;
  }

  // "APELLIDOS APELLIDO2, NOMBRE [NOMBRE2]" — 1 a 3 palabras por lado.
  const NAME_RE = /^[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ.\-']*(?:\s+[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñ.\-']*){0,2},\s*[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑa-záéíóúñª.\-']*(?:\s+[A-ZÁÉÍÓÚÑa-záéíóúñª.\-']+){0,2}$/;

  function pickWorkerName(candidates) {
    for (const raw of (candidates || [])) {
      if (!raw || typeof raw !== 'string') continue;
      // Limpiar decoración típica del árbol: contadores "(2)", espacios dobles
      const cleaned = raw.replace(/\(\d+\)/g, '').replace(/\s+/g, ' ').trim();
      if (!cleaned || cleaned.length > 60) continue;
      if (NAME_RE.test(cleaned)) return cleaned;
    }
    return null;
  }

  // Nodo seleccionado del árbol easyui de Actais (vía page-bridge, API
  // jQuery `tree('getSelected')`) → { id, name } normalizado.
  // El id llega como "w_46" (worker) o "dep_5" (departamento); los nodos sin
  // formato "APELLIDOS, NOMBRE" (departamentos, empresas) se descartan.
  function normalizeBridgeWorker(node) {
    if (!node || typeof node.text !== 'string') return null;
    const name = node.text.replace(/\s+/g, ' ').trim();
    if (!name || !name.includes(',')) return null;
    if (!pickWorkerName([name])) return null;
    let id = null;
    if (node.id != null) {
      const m = String(node.id).match(/^(?:w_?)?(\d+)$/i);
      id = m ? m[1] : null;
      if (!m && !/^w/i.test(String(node.id))) return null; // dep_X, loc_X…
    }
    return { id, name };
  }

  return { absenceCodeFromCell, pickWorkerName, normalizeBridgeWorker, ABSENCE_CODES };
});

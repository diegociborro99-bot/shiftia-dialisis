// ============================================================================
// assembleMonth — convierte las celdas parseadas del DOM de Actais en una
// planilla mensual {workerId, year, month, cells[31]} lista para
// /api/assistant/syncWorkerMonth.
//
// Función pura. UMD: en el navegador cuelga de window.ShiftiaShared,
// en Node se exporta para los tests (tests/month-assembler.test.js).
// ============================================================================
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.ShiftiaShared = Object.assign(root.ShiftiaShared || {}, factory());
  }
})(typeof self !== 'undefined' ? self : this, function () {

  const MIN_CELLS_FOR_FULL_MONTH = 20; // un mes real tiene 28-31; menos = render parcial

  function assembleMonth(parsedCells) {
    const valid = (Array.isArray(parsedCells) ? parsedCells : []).filter(c =>
      c && c.workerId != null &&
      Number.isInteger(c.day) && c.day >= 0 && c.day <= 30 &&
      Number.isInteger(c.month) && c.month >= 0 && c.month <= 11 &&
      Number.isInteger(c.year)
    );
    if (valid.length === 0) {
      return { ok: false, error: 'No se encontraron celdas de calendario parseables' };
    }

    // Grupo dominante por (workerId, year, month) — descarta celdas sueltas
    // de meses colindantes u otros workers que se cuelen en el DOM.
    const groups = new Map();
    for (const c of valid) {
      const key = `${c.workerId}|${c.year}|${c.month}`;
      if (!groups.has(key)) groups.set(key, []);
      groups.get(key).push(c);
    }
    let dominant = null;
    for (const arr of groups.values()) {
      if (!dominant || arr.length > dominant.length) dominant = arr;
    }

    if (dominant.length < MIN_CELLS_FOR_FULL_MONTH) {
      return {
        ok: false,
        error: `Render parcial: solo ${dominant.length} celdas visibles del mes. Espera a que Actais termine de pintar el calendario y reintenta.`
      };
    }

    const { workerId, year, month } = dominant[0];
    const cells = new Array(31).fill('');
    const seen = new Set();
    for (const c of dominant) {
      seen.add(c.day);
      const shift = c.shift ? String(c.shift).trim().toUpperCase() : '';
      // Día duplicado: no pisar un valor existente con vacío
      if (shift || !cells[c.day]) cells[c.day] = shift;
    }

    return {
      ok: true,
      workerId, year, month, cells,
      stats: {
        daysSeen: seen.size,
        filled: cells.filter(Boolean).length
      }
    };
  }

  return { assembleMonth };
});

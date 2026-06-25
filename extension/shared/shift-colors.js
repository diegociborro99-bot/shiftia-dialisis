// ============================================================================
// shift-colors.js — reconocimiento de turno por el COLOR de fondo de la celda
// de Actais. Es el tercer nivel de detección (clase S_X → texto → color):
// cubre los S_X aún no mapeados. Colores verificados con HTML real
// (12-jun-2026) y los PDFs de planificación anual del Hospital de Jove.
// UMD: navegador (window.ShiftiaShared) + Node (tests).
// ============================================================================
(function (root, factory) {
  if (typeof module === 'object' && module.exports) {
    module.exports = factory();
  } else {
    root.ShiftiaShared = Object.assign(root.ShiftiaShared || {}, factory());
  }
})(typeof self !== 'undefined' ? self : this, function () {

  // Paleta confirmada de Actais → código Shiftia
  const PALETTE = [
    { rgb: [32, 121, 121],  code: 'M' }, // verde — Mañanas
    { rgb: [166, 32, 32],   code: 'D' }, // rojo — Descanso
    { rgb: [204, 217, 242], code: 'T' }, // azul claro — Tardes
    { rgb: [255, 217, 64],  code: 'N' }  // amarillo — Noches
  ];

  const TOLERANCE = 18; // distancia máxima por canal (ruido de render)

  function shiftFromColor(cssColor) {
    if (!cssColor || typeof cssColor !== 'string') return null;
    const m = cssColor.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/);
    if (!m) return null;
    const c = [parseInt(m[1], 10), parseInt(m[2], 10), parseInt(m[3], 10)];
    for (const p of PALETTE) {
      if (Math.abs(c[0] - p.rgb[0]) <= TOLERANCE &&
          Math.abs(c[1] - p.rgb[1]) <= TOLERANCE &&
          Math.abs(c[2] - p.rgb[2]) <= TOLERANCE) {
        return p.code;
      }
    }
    return null;
  }

  return { shiftFromColor };
});

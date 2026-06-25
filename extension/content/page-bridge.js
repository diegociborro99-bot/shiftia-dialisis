// ============================================================================
// page-bridge.js — corre en el MUNDO DE LA PÁGINA (no en el isolated world
// del content script) para acceder al estado interno de Actais.
//
// Fuente de verdad del trabajador seleccionado: la API del árbol easyui,
// jQuery(tree).tree('getSelected') → { id: "w_46", text: "APELLIDOS, NOMBRE" }.
// Verificado en vivo contra Actais el 12-jun-2026. Cada vez que cambia la
// selección se publica vía postMessage; detector.js (isolated world) lo cachea.
// ============================================================================
(function () {
  if (window.__shiftiaBridgeInstalled) return;
  window.__shiftiaBridgeInstalled = true;

  function readSelectedWorker() {
    try {
      const $ = window.jQuery;
      if (!$ || !$.fn || !$.fn.tree) return null;
      for (const t of $('[name="tree"], .easyui-tree').toArray()) {
        try {
          const sel = $(t).tree('getSelected');
          if (sel && sel.text) return { id: sel.id ?? null, text: String(sel.text) };
        } catch (_) { /* instancia sin tree() inicializado */ }
      }
    } catch (_) {}
    return null;
  }

  let lastSig = null;
  function publish() {
    const worker = readSelectedWorker();
    const sig = JSON.stringify(worker);
    if (sig === lastSig) return;
    lastSig = sig;
    window.postMessage({ source: 'shiftia-bridge', type: 'selectedWorker', payload: worker }, '*');
  }

  // La selección cambia con clics en el árbol; el setInterval es la red de
  // seguridad para cambios programáticos (búsqueda, teclado). Solo publica
  // cuando la selección realmente cambia, así que el coste es mínimo.
  document.addEventListener('click', () => setTimeout(publish, 400), true);
  setInterval(publish, 3000);
  publish();
})();

// Service worker de Shiftia · Diálisis.
// 1) abre el panel al pulsar el icono;
// 2) enruta las preguntas del menú Alt+clic (content/detector.js) al BACKEND
//    en la nube (/api/assistant/{action}) con el token de sesión. El backend
//    es el cerebro; aquí solo se traduce la respuesta a texto legible.
//
// La URL del backend y el token se guardan en chrome.storage.local
// (engineUrl, shiftiaToken) desde el panel lateral.

chrome.runtime.onInstalled.addListener(() => {
  chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
});

async function getBase() {
  return ((await chrome.storage.local.get("engineUrl")).engineUrl || "").replace(/\/+$/, "");
}
async function getToken() {
  return (await chrome.storage.local.get("shiftiaToken")).shiftiaToken;
}

const sur = (n) => (n || "").split(",")[0].trim();

function formatForMenu(action, d) {
  if (action === "librar" || action === "release")
    return (d.ok ? "✅ Sí puede librar.\n" : "❌ No puede librar.\n") + (d.reason || "");
  if (action === "whoCovers" || action === "cover" || action === "vacaciones") {
    const gaps = d.gaps || [];
    if (!gaps.length) return "✅ Cubierto: no se rompe la cobertura mínima.";
    return gaps.map((g) => {
      const f = g.need - g.have;
      const c = (g.candidates || []).slice(0, 5).map((x) => sur(x.name)).join(", ");
      return `⚠️ Faltan ${f} en ${g.shift}${g.skill ? "/" + g.skill : ""} (día ${g.day + 1}).\n` +
             (c ? "Pueden cubrir: " + c : "Sin sustituto directo legal — reorganizar.");
    }).join("\n\n");
  }
  if (action === "validateConvenio" || action === "validar") {
    if (d.compliant) return "✅ La planilla cumple el convenio.";
    return "❌ No cumple:\n" + (d.checks || []).filter((c) => c.status !== "pass")
      .map((c) => "• " + c.id + (c.issues && c.issues[0] ? " — " + c.issues[0] : "")).join("\n");
  }
  if (action === "cambio")
    return "Para proponer un cambio marca dos días en el panel de Shiftia (icono de la extensión).";
  return "Acción no disponible. Usa: Librar, ¿Quién cubre? o Validar convenio.";
}

async function ask(action, cell) {
  const base = await getBase();
  const token = await getToken();
  if (!base) return "⚙️ Configura la URL del motor en el panel (icono de la extensión).";
  if (!token) return "🔒 Inicia sesión en el panel de Shiftia (icono de la extensión).";
  if (cell.day == null || !cell.worker) return "No identifico el trabajador/día de esa celda.";

  let res;
  try {
    res = await fetch(`${base}/api/assistant/${action}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "Authorization": "Bearer " + token },
      body: JSON.stringify({ cell: { worker: cell.worker, worker_name: cell.worker, day: cell.day } })
    });
  } catch (e) {
    return "⚠️ No conecto con el motor (" + base + "). Revisa la URL o tu conexión.";
  }
  if (res.status === 401) {
    await chrome.storage.local.remove("shiftiaToken");
    chrome.runtime.sendMessage({ type: "panel:sessionExpired" }).catch(() => {});
    return "🔒 Sesión caducada. Vuelve a iniciar sesión en el panel.";
  }
  if (!res.ok) {
    let m = "Error " + res.status;
    try { const b = await res.json(); if (b.detail) m = b.detail; } catch (_) {}
    return "⚠️ " + m;
  }
  return formatForMenu(action, await res.json());
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "shiftia:askEngine") {
    ask(msg.payload.action, msg.payload.args)
      .then((text) => sendResponse({ ok: true, data: text }))
      .catch((e) => sendResponse({ ok: false, error: e.message }));
    return true;
  }
  return false;
});

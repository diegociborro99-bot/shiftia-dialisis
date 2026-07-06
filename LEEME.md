# Shiftia · Diálisis

Funciona **como la otra extensión**: el cerebro (motor) vive en la **nube** y la
extensión le habla. Noelia **no arranca nada**.

Dos piezas:

- **El backend** (carpeta `server/`) — el motor + la base de datos donde se
  **guardan las planillas** (no se borran). Se despliega una vez en Railway
  (ver `DESPLIEGUE.md`).
- **La extensión** (carpeta `extension/`) — lo que usa Noelia: un panel propio
  + el menú **Alt+clic** dentro de Actais. Habla con el backend.

## Puesta en marcha

**Una vez (tú):** despliega el backend siguiendo **`DESPLIEGUE.md`**.

**Cargar la extensión en Chrome:**
- `chrome://extensions` → "Modo de desarrollador" → "Cargar descomprimida"
- Elige la carpeta **`shiftia-dialisis/extension`** (la subcarpeta `extension`, NO la raíz).

**Primer uso (Noelia):**
1. Pulsa el icono de Shiftia → se abre el panel.
2. Pega la **URL del motor** (la de Railway), usuario **NOEMONTS** y contraseña → Entrar.
3. Sube el **PDF** de la planilla (se guarda en la nube).
4. Listo. A partir de aquí solo abre el panel: la planilla ya está.

## Cómo se usa

- **Clic en un día** del calendario → ¿puede librar? · ¿quién cubre? · marcar para cambio.
- Dentro de **Actais**: **Alt + clic** en una celda → mismo menú, sobre la planilla guardada.
- **＋ PDF** para actualizar el mes (vuelve a leer el PDF y lo guarda).
- **🔄 Sincronizar**: si la supervisora cambió algo en Actais, con el calendario
  del trabajador en pantalla pulsa 🔄 en el panel. Shiftia lee la pantalla,
  **muestra las diferencias** con la planilla guardada y solo al confirmar las
  aplica (a NUESTRA base de datos — Actais no se toca nunca). También celda a
  celda con Alt+clic → «Volcar cambio».
- **🕘 Historial**: cada PDF, sincronización o restauración deja una versión.
  Cualquier cambio se puede **deshacer** desde ahí.

Si al sincronizar aparece el aviso «días que no se pudieron leer», Actais está
usando un código de turno que la extensión aún no conoce: apúntalo (sale la
clase `S_XX`) para añadirlo al mapa de `extension/content/detector.js`.

## Por qué no se borran las planillas

Viven en una **base de datos Postgres** en la nube, con **historial de
versiones** (tabla `planilla_versions`). Sobreviven a reinicios y redeploys, y
cada cambio queda registrado y es reversible. La extensión solo cachea una
copia para ir rápida, pero la verdad está en la BD.

## Variables de entorno (Railway → Variables)

- `DATABASE_URL` — Postgres (obligatoria en la nube).
- `JWT_SECRET` — secreto de firma de sesiones (obligatoria; si falta se usa
  una de desarrollo y el servidor lo avisa por log).
- `SHIFTIA_USER` / `SHIFTIA_PASS_HASH` — credenciales de acceso. El hash se
  genera con:
  `python -c "import hashlib;print(hashlib.sha256('USUARIO:clave'.encode()).hexdigest())"`
  Definirlas permite **rotar la contraseña sin tocar código**.
- `ALLOWED_ORIGINS` — (opcional) orígenes CORS separados por comas, p. ej.
  `chrome-extension://<id-de-la-extension>`.

## Las normas de diálisis (ya configuradas)

- Turno **MT** de 12 h (07:00–19:00).
- Mínimo **5 enfermeras** por turno (sin contar a la supervisora).
- **Máximo 2 días seguidos** trabajados.
- Descanso mínimo **12 h** entre turnos.
- **Domingos cerrado**.
- Supervisora (Noelia) con **mañanas fijas**.

## Estructura

```
shiftia-dialisis/
├─ extension/        ← carga ESTO en Chrome
├─ server/           ← el backend (FastAPI + motor + BD)
│  ├─ main.py  db.py  auth.py  engine_api.py
│  └─ requirements.txt
├─ Dockerfile  railway.toml  prepare_deploy.sh
├─ DESPLIEGUE.md     ← cómo subirlo a Railway
└─ (app.py, ui/ — el panel local antiguo, opcional para pruebas sin nube)
```

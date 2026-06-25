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
- **＋ Subir planilla** para actualizar el mes (vuelve a leer el PDF y lo guarda).

## Por qué no se borran las planillas

Viven en una **base de datos Postgres** en la nube. Sobreviven a reinicios y
redeploys. La extensión solo cachea una copia para ir rápida, pero la verdad
está en la BD.

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

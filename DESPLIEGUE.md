# Desplegar el motor de Diálisis en Railway (una sola vez)

El backend guarda las planillas en **Postgres** (no se borran) y responde a la
extensión. Tras esto, Noelia **no arranca nada**: la extensión habla con la nube.

## Pasos

**1. Vendoriza el motor** (copia shiftia-core dentro de la carpeta):

```bash
cd shiftia-dialisis
./prepare_deploy.sh
```

**2. Sube la carpeta `shiftia-dialisis` a un repo de GitHub.**
(Asegúrate de que `vendor/` se incluye en el commit — el `.gitignore` no lo excluye.)

**3. En Railway:**

1. **New Project → Deploy from GitHub repo** → elige el repo. Railway detecta el `Dockerfile`.
2. **+ New → Database → Add PostgreSQL.** Railway crea la BD.
3. En el **servicio** (no la BD) → pestaña **Variables**:
   - `DATABASE_URL` → ponla a `${{Postgres.DATABASE_URL}}` (referencia a la BD del paso 2).
   - `JWT_SECRET` → una cadena larga y aleatoria (firma de los tokens). Inventa una larga.
4. **Deploy.** Railway construye con el Dockerfile.

**4. Copia la URL pública** del servicio (algo como `https://shiftia-dialisis-production.up.railway.app`).

**5. En la extensión:** abre el panel (icono de Shiftia) → pega esa **URL del motor**, usuario **NOEMONTS** y la contraseña → **Entrar**. La primera vez, **sube el PDF** de la planilla; queda guardado en la nube.

## Variables de entorno (resumen)

| Variable | Para qué | Valor |
|---|---|---|
| `DATABASE_URL` | Dónde se guardan las planillas | `${{Postgres.DATABASE_URL}}` (Railway) |
| `JWT_SECRET` | Firma de los tokens de sesión | una cadena larga secreta |
| `PORT` | Puerto | lo pone Railway solo |

## Comprobar que va

- Abre `https://TU-URL/` en el navegador → debe responder `{"ok": true, "service": "shiftia-dialisis"}`.
- En el panel, tras entrar y subir el PDF, verás la auditoría y el calendario.

## Cambiar de usuario / contraseña

El usuario y el hash de la contraseña están en `server/auth.py` (`USER`, `PASS_HASH`).
El hash es `sha256("USUARIO:contraseña")`. Para cambiarlo, genera el nuevo hash y
sustitúyelo (nunca se guarda la contraseña en claro).

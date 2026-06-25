<p align="center"><img src="../shiftia-core/assets/shiftia-logo.svg" width="72" height="72" alt="Shiftia"></p>

# Shiftia Diálisis

Guante (cliente) para planificar una **unidad de diálisis** sobre el motor
genérico **`shiftia-core`**. No duplica el motor: lo **reutiliza**. Aquí solo
viven los datos del servicio — turnos, roles y reglas de la unidad.

## Servicio

- **Roles:** enfermeras y auxiliares (modelados como `groups`/`skills`).
- **Turnos de 12 h.**
- Reglas y cobertura: **se fijan a partir de la planilla de ejemplo** del cliente
  (pendiente de cargar — ver `dialisis.py`, sección CONFIG).

## Arquitectura

Cliente fino: importa `shiftiacore` del proyecto hermano `../shiftia-core`. La
lógica de optimización (CP-SAT, reglas, auditoría, etc.) **vive una sola vez** en
el motor; este repo solo describe el "guante" de diálisis.

```
shiftia-dialisis/
├── dialisis.py     ← define turnos, roles y reglas de la unidad (el guante)
├── _bootstrap.py   ← engancha el motor shiftia-core (import shiftiacore)
└── README.md
```

> Para producción se empaquetaría `shiftia-core` como dependencia (pip); en local
> se importa por ruta del proyecto hermano.

## Uso (cuando esté configurado)

```bash
python dialisis.py        # genera y muestra una planilla de la unidad
```

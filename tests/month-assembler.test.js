// Tests del ensamblador de mes (extension/shared/month-assembler.js).
// Sin dependencias:  node tests/month-assembler.test.js
const assert = require('assert');
const { assembleMonth } = require('../extension/shared/month-assembler.js');

function cell(day, shift, extra = {}) {
  return { workerId: '1122', day, month: 9, year: 2026, shift, ...extra };
}

// Mes completo reconocido
{
  const cells = Array.from({ length: 31 }, (_, d) => cell(d, d % 3 ? 'MT' : 'D'));
  const r = assembleMonth(cells);
  assert.ok(r.ok);
  assert.strictEqual(r.workerId, '1122');
  assert.strictEqual(r.cells.length, 31);
  assert.strictEqual(r.stats.filled, 31);
  assert.deepStrictEqual(r.unknownDays, []);
  console.log('  ok  mes completo');
}

// Celdas con contenido NO reconocido → unknownDays con detalle, no silencio
{
  const cells = Array.from({ length: 30 }, (_, d) => cell(d, 'MT'));
  cells[4] = cell(4, null, { hadContent: true, rawClass: 'S_99', scheduleFull: 'Turno misterioso' });
  cells[9] = cell(9, null, { hadContent: false }); // vacía de verdad: no es unknown
  const r = assembleMonth(cells);
  assert.ok(r.ok);
  assert.strictEqual(r.unknownDays.length, 1);
  assert.strictEqual(r.unknownDays[0].day, 4);
  assert.strictEqual(r.unknownDays[0].rawClass, 'S_99');
  assert.strictEqual(r.stats.unknown, 1);
  console.log('  ok  unknownDays');
}

// Render parcial (< 20 celdas) → error explícito
{
  const r = assembleMonth(Array.from({ length: 10 }, (_, d) => cell(d, 'MT')));
  assert.ok(!r.ok && /parcial/i.test(r.error));
  console.log('  ok  render parcial');
}

// Grupo dominante: descarta celdas sueltas de otro trabajador
{
  const cells = Array.from({ length: 28 }, (_, d) => cell(d, 'MT'));
  cells.push({ workerId: '999', day: 0, month: 9, year: 2026, shift: 'D' });
  const r = assembleMonth(cells);
  assert.ok(r.ok && r.workerId === '1122');
  console.log('  ok  grupo dominante');
}

console.log('\ntests OK');

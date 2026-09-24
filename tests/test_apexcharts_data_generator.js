/**
 * Real JavaScript execution of ApexCharts card data_generator scripts from
 * IMPLEMENTATION_PLAN_GAS_INTERPOLATION.md Section 3.1.
 *
 * Runs natively in Node.js v26 to verify:
 * 1. Exact execution of Section 3.1 data_generator code.
 * 2. Proper Europe/Budapest local calendar day resolution during CEST (summer UTC+2) and CET (winter UTC+1).
 * 3. Accurate separation into CLOSED (#1e88e5) and PARTIAL (#ffa726) series.
 * 4. Proper null-filling preventing double-counting or missing bars.
 */
import assert from 'node:assert';

console.log('--- Testing ApexCharts data_generator scripts in real Node.js ---');

// Mock Hass environment
const mockHass = {
  callWS: async (msg) => {
    if (msg.type === 'recorder/statistics_during_period') {
      return {
        'gas_photo:gas_estimated': [
          // 2026-10-04 22:00:00 UTC = 2026-10-05 00:00:00 CEST (Budapest midnight)
          { start: '2026-10-04T22:00:00.000Z', change: 0.850 },
          // 2026-10-05 22:00:00 UTC = 2026-10-06 00:00:00 CEST
          { start: '2026-10-05T22:00:00.000Z', change: 1.250 },
          // 2026-10-06 22:00:00 UTC = 2026-10-07 00:00:00 CEST
          { start: '2026-10-06T22:00:00.000Z', change: 1.200 },
          // 2026-10-07 22:00:00 UTC = 2026-10-08 00:00:00 CEST
          { start: '2026-10-07T22:00:00.000Z', change: 0.400 },
          // 2026-10-08 22:00:00 UTC = 2026-10-09 00:00:00 CEST
          { start: '2026-10-08T22:00:00.000Z', change: 0.000 },
          // Winter test: 2026-01-14 23:00:00 UTC = 2026-01-15 00:00:00 CET (Budapest midnight)
          { start: '2026-01-14T23:00:00.000Z', change: 3.500 },
        ]
      };
    }
    throw new Error('Unknown WS call');
  }
};

const mockEntity = {
  attributes: {
    published_revision_id: 2,
    published_daily_coverage: {
      '2026-10-05': 'PARTIAL',
      '2026-10-06': 'CLOSED',
      '2026-10-07': 'CLOSED',
      '2026-10-08': 'PARTIAL',
      '2026-10-09': 'OPEN',
      '2026-01-15': 'CLOSED',
    }
  }
};

const start = new Date('2026-10-01T00:00:00Z');
const end = new Date('2026-10-15T00:00:00Z');

// Exact code from Spec Section 3.1: Series 1 (CLOSED)
async function generateClosedSeries(hass, entity, start, end) {
  const startIso = start.toISOString();
  const endIso = end.toISOString();
  // A trigger entitas attributumabol olvassuk a publikalt revizio lefedettsegi pillanatkepet:
  const coverageMap = entity.attributes.published_daily_coverage || {};
  const result = await hass.callWS({
    type: 'recorder/statistics_during_period',
    statistic_ids: ['gas_photo:gas_estimated'],
    period: 'day',
    types: ['change'],
    start_time: startIso,
    end_time: endIso,
  });
  const stats = result['gas_photo:gas_estimated'] || [];
  return stats.map(entry => {
    // Explicit Europe/Budapest helyi naptari nap kepzese (teli UTC+1 es nyari UTC+2 kezelese):
    const dStr = new Date(entry.start).toLocaleDateString('en-CA', { timeZone: 'Europe/Budapest' });
    const isClosed = coverageMap[dStr] === 'CLOSED';
    const val = (isClosed && entry.change !== null && entry.change !== undefined) ? Number(entry.change) : null;
    return [new Date(entry.start).getTime(), val];
  });
}

// Exact code from Spec Section 3.1: Series 2 (PARTIAL)
async function generatePartialSeries(hass, entity, start, end) {
  const startIso = start.toISOString();
  const endIso = end.toISOString();
  const coverageMap = entity.attributes.published_daily_coverage || {};
  const result = await hass.callWS({
    type: 'recorder/statistics_during_period',
    statistic_ids: ['gas_photo:gas_estimated'],
    period: 'day',
    types: ['change'],
    start_time: startIso,
    end_time: endIso,
  });
  const stats = result['gas_photo:gas_estimated'] || [];
  return stats.map(entry => {
    const dStr = new Date(entry.start).toLocaleDateString('en-CA', { timeZone: 'Europe/Budapest' });
    const isPartial = coverageMap[dStr] === 'PARTIAL';
    const val = (isPartial && entry.change !== null && entry.change !== undefined) ? Number(entry.change) : null;
    return [new Date(entry.start).getTime(), val];
  });
}

async function runTests() {
  const closedData = await generateClosedSeries(mockHass, mockEntity, start, end);
  const partialData = await generatePartialSeries(mockHass, mockEntity, start, end);

  console.log('Closed series output:', closedData);
  console.log('Partial series output:', partialData);

  // Assertion 1: Day 2026-10-05 is PARTIAL -> Closed is null, Partial is 0.850
  assert.strictEqual(closedData[0][1], null, '2026-10-05 must be null in CLOSED series');
  assert.strictEqual(partialData[0][1], 0.850, '2026-10-05 must be 0.850 in PARTIAL series');

  // Assertion 2: Day 2026-10-06 is CLOSED -> Closed is 1.250, Partial is null
  assert.strictEqual(closedData[1][1], 1.250, '2026-10-06 must be 1.250 in CLOSED series');
  assert.strictEqual(partialData[1][1], null, '2026-10-06 must be null in PARTIAL series');

  // Assertion 3: Day 2026-10-07 is CLOSED -> Closed is 1.200, Partial is null
  assert.strictEqual(closedData[2][1], 1.200, '2026-10-07 must be 1.200 in CLOSED series');
  assert.strictEqual(partialData[2][1], null, '2026-10-07 must be null in PARTIAL series');

  // Assertion 4: Day 2026-10-08 is PARTIAL -> Closed is null, Partial is 0.400
  assert.strictEqual(closedData[3][1], null, '2026-10-08 must be null in CLOSED series');
  assert.strictEqual(partialData[3][1], 0.400, '2026-10-08 must be 0.400 in PARTIAL series');

  // Assertion 5: Day 2026-10-09 is OPEN -> Both are null
  assert.strictEqual(closedData[4][1], null, '2026-10-09 must be null in CLOSED series');
  assert.strictEqual(partialData[4][1], null, '2026-10-09 must be null in PARTIAL series');

  // Assertion 6: Winter date 2026-01-14T23:00:00Z -> Local 2026-01-15 is CLOSED
  assert.strictEqual(closedData[5][1], 3.500, 'Winter 2026-01-15 must be 3.500 in CLOSED series');
  assert.strictEqual(partialData[5][1], null, 'Winter 2026-01-15 must be null in PARTIAL series');

  // Invariant Assertion: Mutual exclusivity for all items
  for (let i = 0; i < closedData.length; i++) {
    const cVal = closedData[i][1];
    const pVal = partialData[i][1];
    assert(!(cVal !== null && pVal !== null), `Mutual exclusivity violated at index ${i}`);
  }

  console.log(' All JavaScript data_generator tests PASSED successfully!');
}

runTests().catch(err => {
  console.error('Test failed:', err);
  process.exit(1);
});

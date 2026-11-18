/* X10: pure maths for the Approved Windows modal.
 *
 * Loads the shipped app.js through the shared stub-browser loader, then calls
 * only the DOM-free helpers (winPreset, winOnceRange, winDailySchedule,
 * winPreviewText). Run once per TZ by the Python test:
 *
 *   TZ=Europe/London node tests/ui/windows_modal.cjs
 *   TZ=America/New_York node tests/ui/windows_modal.cjs
 *
 * Prints one JSON object on stdout. Exit 2 means Node is present but the
 * harness could not load; any other non-zero exit is a real failure.
 */
'use strict';

const { load } = require('../fixtures/appjs_loader');

let ctx;
try {
  ctx = load();
} catch (e) {
  process.stderr.write('load-failed: ' + e.message + '\n');
  process.exit(2);
}

function call(name, ...args) {
  const fn = ctx[name];
  if (typeof fn !== 'function') throw new Error('missing helper: ' + name);
  return fn.apply(null, args);
}

const now2250 = new Date(2026, 10, 17, 22, 50, 0, 0).getTime();
const now1500 = new Date(2026, 10, 17, 15, 0, 0, 0).getTime();
const now2350 = new Date(2026, 10, 17, 23, 50, 0, 0).getTime();

const preset15 = call('winPreset', 'now15', now2250);
const preset60 = call('winPreset', 'now60', now2250);
const endOfDay = call('winPreset', 'end_of_today', now1500);
const crossing = call('winOnceRange', '23:50', 30, now2350);
const pastOnce = call('winOnceRange', '14:30', 15, now1500);
const daily = call('winDailySchedule', '22:50', 0, now2250);
const dailyWeekdays = call('winDailySchedule', '07:05', 31, now2250);

const oncePreview = call('winPreviewText', {
  window_start: crossing.start, window_end: crossing.end,
  max_executions: 1,
}, { nowMs: now2350, gateway: 'gateway-one' });

const onceSameDay = call('winPreviewText', {
  window_start: Math.floor(now2250 / 1000),
  window_end: Math.floor(now2250 / 1000) + 15 * 60,
  max_executions: 1,
}, { nowMs: now2250, gateway: 'gateway-one' });

const openEndedPreview = call('winPreviewText', {
  window_start: Math.floor(now2250 / 1000), window_end: 4102444800,
  max_executions: 1,
}, { nowMs: now2250, gateway: 'gateway-one' });

const dailyPreview = call('winPreviewText', {
  days_of_week: 0, execution_time: daily.execution_time,
  max_executions: 1, expires_at: 0,
}, { nowMs: now2250, gateway: 'gateway-one' });

const weeklyPreview = call('winPreviewText', {
  days_of_week: 31, execution_time: dailyWeekdays.execution_time,
  max_executions: 2, expires_at: 0,
}, { nowMs: now2250, gateway: 'gateway-one' });

console.log(JSON.stringify({
  tz: process.env.TZ || '',
  now2250,
  now1500,
  preset15,
  preset60,
  endOfDay,
  crossing,
  past_once: pastOnce,
  daily,
  dailyWeekdays,
  previews: {
    once: oncePreview,
    once_same_day: onceSameDay,
    daily: dailyPreview,
    weekly: weeklyPreview,
    open_ended: openEndedPreview,
  },
}));
process.exit(0);

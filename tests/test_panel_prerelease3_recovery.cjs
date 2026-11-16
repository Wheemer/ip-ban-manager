const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync(process.env.IPBM_PANEL_SOURCE || `${__dirname}/../custom_components/ip_ban_manager/panel.js`, 'utf8');
let Panel;
vm.runInNewContext(source, {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  window: { clearTimeout() {}, setTimeout() {} },
});
(async () => {
  const panel = new Panel();
  const changes = panel._changedOptionKeys(
    { settings: { auto_ban_enabled: false, public_region_rules: {} } },
    { settings: { auto_ban_enabled: true, public_region_rules: { CA: 5 } } },
    { auto_ban_enabled: true }
  );
  assert.deepEqual(Array.from(changes), ['auto_ban_enabled'], 'Toasts must not attribute unrelated changes to this save');
  panel._renderToast = panel._storeToast = panel._scheduleToastClear = () => {};
  panel._numericErrors = new Map([['threshold', 'Rejected']]);
  panel._showToast('Threshold rejected', 'error');
  panel._showToast('History saved', 'success');
  assert.equal(panel._toast, 'Threshold rejected', 'An independent save must not erase an outstanding numeric error');
  panel._isEditing = () => false;
  panel._renderSafely = () => {};
  panel._statusPath = () => 'status';
  panel._withTimeout = promise => promise;
  let settle;
  panel._api = () => new Promise(resolve => { settle = resolve; });
  panel._numericSaveQueue = new Map([['activity_history_hours', 48]]);
  let flushes = 0;
  panel._scheduleNumericFlush = () => flushes++;
  const pending = panel._load();
  panel._updateGeneration = 1; // Detach invalidates an in-flight load.
  panel._detachedGeneration = 1;
  settle({ settings: {} });
  await pending;
  assert.equal(panel._busy, false, 'Detached loads must release their own busy flag');
  assert.equal(flushes, 1, 'Queued edits must be resumed after a load settles');
  console.log('Pre-release 3 regressions: scoped toasts, retained errors, detached load recovery passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

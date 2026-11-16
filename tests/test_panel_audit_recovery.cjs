const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let Panel;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../custom_components/ip_ban_manager/panel.js'), 'utf8'), {
  HTMLElement: class {},
  console: { error() {} },
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  window: { setTimeout: () => 1, clearTimeout() {} },
});

(async () => {
  const panel = new Panel();
  panel._data = { settings: { auto_ban_enabled: false } };
  panel._captureFormDrafts = panel._captureOptionDrafts = () => new Map();
  panel._restoreFormDrafts = panel._restoreOptionDrafts = panel._showFieldError = () => {};
  panel._render = panel._renderIncremental = panel._showToast = () => {};
  panel._language = () => 'en';
  panel._statusPath = () => 'status';
  let resolvePost;
  let refreshes = 0;
  panel._scheduleLoad = () => refreshes++;
  panel._api = method => method === 'POST'
    ? new Promise(resolve => { resolvePost = resolve; })
    : Promise.resolve({ settings: { auto_ban_enabled: true } });
  let first = true;
  panel._withTimeout = promise => {
    if (first) { first = false; return Promise.reject(new Error('Timed out')); }
    return promise;
  };
  assert.equal(await panel._post('set_options', { options: { auto_ban_enabled: true } }), false);
  assert.equal(panel._data.settings.auto_ban_enabled, true, 'Failed writes must reconcile server state');
  resolvePost({});
  await Promise.resolve();
  assert.equal(refreshes, 1, 'A late commit must trigger a reconciliation refresh');

  panel._busy = true;
  panel._render = () => { throw new Error('Malformed optional section'); };
  panel._renderSafely();
  assert.equal(panel._busy, true, 'Render failures cannot release an active mutation lock');

  panel._data = { settings: { login_attempts_threshold: 5 } };
  panel._numericSaveInFlight = { login_attempts_threshold: 7 };
  const input = { id: 'threshold', value: '7', checkValidity: () => true, removeAttribute() {} };
  panel._queueNumericSave(input);
  assert.equal(panel._numericSaveQueue.size, 0, 'Blur must not queue a duplicate in-flight value');
  input.value = '8';
  panel._queueNumericSave(input);
  assert.equal(panel._numericSaveQueue.get('login_attempts_threshold'), 8, 'New edits must remain queued');
  console.log('Audit recovery: server reconciliation, late completion, render lock, and duplicate numeric write checks passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

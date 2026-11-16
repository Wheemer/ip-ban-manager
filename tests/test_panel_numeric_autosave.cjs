const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let Panel;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../custom_components/ip_ban_manager/panel.js'), 'utf8'), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  window: { setTimeout: () => 1, clearTimeout: () => {} },
});
(async () => {
  const panel = new Panel();
  panel._data = {
    settings: { login_attempts_threshold: 5, activity_history_hours: 24 },
    translations: {
      'settings.login_attempts_threshold': 'Login attempts threshold',
      'settings.activity_history_hours': 'History retention (hours)',
      'settings.auto_ban_enabled': 'Automatic bans',
      'success.set_options': 'Options applied.',
    },
  };
  panel._hass = { locale: { language: 'en' } };
  panel._showToast = (message, type) => { panel.notice = { message, type }; };
  let posts = [];
  panel._post = async (...args) => { posts.push(args); return true; };
  const input = {
    id: 'threshold', value: '', checkValidity: () => false,
    validationMessage: 'Enter a number.', setAttribute: () => {}, removeAttribute: () => {},
  };
  panel._queueNumericSave(input);
  await panel._flushNumericSaves();
  assert.equal(posts.length, 0);
  assert.equal(panel.notice.type, 'error');
  input.value = '7';
  input.checkValidity = () => true;
  panel._queueNumericSave(input);
  panel._busy = true;
  await panel._flushNumericSaves();
  assert.equal(posts.length, 0);
  input.value = '8';
  panel._queueNumericSave(input);
  panel._busy = false;
  await panel._flushNumericSaves();
  assert.equal(posts.length, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0][1])), { options: { login_attempts_threshold: 8 } });
  assert.equal(posts[0][2], true, 'Numeric saves must preserve the editor DOM');
  panel._data.settings.login_attempts_threshold = 8;
  panel._queueNumericSave(input);
  await panel._flushNumericSaves();
  assert.equal(posts.length, 1, 'Enter followed by blur must not duplicate a saved write');
  const before = { settings: { login_attempts_threshold: 5, activity_history_hours: 24, auto_ban_enabled: false } };
  const after = { settings: { login_attempts_threshold: 8, activity_history_hours: 36, auto_ban_enabled: true } };
  const message = panel._successMessage('set_options', before, after, { options: after.settings });
  assert.match(message, /Login attempts threshold: 8/);
  assert.match(message, /History retention \(hours\): 36/);
  assert.match(message, /Automatic bans: Enabled/);
  console.log('Numeric autosave: validation, bounded coalescing, duplicate suppression, and named saved-value toasts passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

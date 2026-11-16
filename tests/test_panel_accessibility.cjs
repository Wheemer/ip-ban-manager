const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let Panel;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../custom_components/ip_ban_manager/panel.js'), 'utf8'), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  window: {},
  CSS: { escape: value => value },
});

(async () => {
  const panel = new Panel();
  panel._hass = { locale: { language: 'en' } };
  const menu = {};
  panel.shadowRoot = { getElementById: () => menu };
  panel.narrow = true;
  assert.equal(menu.hass, panel._hass);
  assert.equal(menu.narrow, true);
  panel.narrow = false;
  assert.equal(menu.narrow, false);

  const toggle = { tagName: 'HA-SWITCH', checked: true };
  panel._controlBaselines = new WeakMap([[toggle, true]]);
  assert.equal(panel._controlChanged(toggle), false);
  toggle.checked = false;
  assert.equal(panel._controlChanged(toggle), true);

  let ready;
  let focused = false;
  const control = {
    updateComplete: new Promise(resolve => { ready = resolve; }),
    isConnected: true,
    focus: () => { focused = true; },
  };
  panel._focusControl(control);
  assert.equal(focused, false);
  let writes = 0;
  const toggleControl = { id: 'test-toggle', checked: true };
  panel.shadowRoot.querySelector = () => toggleControl;
  panel._post = async () => { writes++; return true; };
  panel._confirm = async () => false;
  await panel._saveToggle(toggleControl, 'test_setting', false, 'Confirm');
  assert.equal(toggleControl.checked, false);
  assert.equal(writes, 0);
  toggleControl.checked = true;
  panel._restoringDrafts = true;
  await panel._saveToggle(toggleControl, 'test_setting', false);
  assert.equal(writes, 0);
  panel._restoringDrafts = false;
  panel._post = async () => { writes++; return false; };
  await panel._saveToggle(toggleControl, 'test_setting', false);
  assert.equal(toggleControl.checked, false);
  assert.equal(writes, 1);
  console.log('Toggle autosave: confirmation cancel, draft replay suppression, and failure rollback passed.');
  ready();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(focused, true);
  focused = false;
  control.isConnected = false;
  panel._focusControl(control);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(focused, false);
  console.log('Accessibility helpers: mobile menu, native switch drafts, deferred focus, detached control safety passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

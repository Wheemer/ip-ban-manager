const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let Panel;
const values = new Map();
const timers = new Set();
const sessionStorage = {
  getItem: key => values.get(key) ?? null,
  setItem: (key, value) => values.set(key, value),
  removeItem: key => values.delete(key),
};

vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, '../custom_components/ip_ban_manager/panel.js'), 'utf8'),
  {
    HTMLElement: class {},
    customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
    URLSearchParams,
    window: {
      sessionStorage,
      setTimeout: (fn, ms) => {
        const timer = setTimeout(fn, ms);
        timers.add(timer);
        return timer;
      },
      clearTimeout: timer => {
        clearTimeout(timer);
        timers.delete(timer);
      },
      setInterval: () => 0,
      clearInterval: () => {},
    },
  }
);

const first = new Panel();
first._renderToast = () => {};
first._showToast('Show in sidebar applied.', 'success');
assert.ok(sessionStorage.getItem('ip_ban_manager.pending_toast'));

// Simulate Home Assistant replacing the panel element while changing sidebar state.
first.disconnectedCallback();
const replacement = new Panel();
replacement._renderToast = () => {};
replacement._restoreToast();

assert.equal(replacement._toast, 'Show in sidebar applied.');
assert.equal(replacement._toastType, 'success');
assert.ok(replacement._toastTimer);

replacement.disconnectedCallback();
for (const timer of timers) clearTimeout(timer);
console.log('Toasts survive Home Assistant panel re-registration for their remaining duration.');

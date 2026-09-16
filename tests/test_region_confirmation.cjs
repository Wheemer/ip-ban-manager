const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const component = path.join(__dirname, '../custom_components/ip_ban_manager');
let Panel;
let accepted = false;
let message;
vm.runInNewContext(fs.readFileSync(path.join(component, 'panel.js'), 'utf8'), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  Intl,
  window: { confirm: text => { message = text; return accepted; } },
});
const translations = {};
function flatten(value, prefix = '') {
  for (const [key, item] of Object.entries(value)) {
    const name = prefix ? `${prefix}.${key}` : key;
    if (typeof item === 'object') flatten(item, name);
    else translations[name] = item;
  }
}
flatten(JSON.parse(fs.readFileSync(path.join(component, 'panel_translations/en.json'), 'utf8')));
const panel = new Panel();
panel._data = {
  translations,
  geoip: { local_region: { subdivision_code: 'CA-NL', subdivision_label: 'Newfoundland and Labrador' } },
};
const added = panel._regionConfirmation({ CA: 5, 'CA-NL': 3 }, true);
assert.match(added, /Canada \(CA\): 5 failed login attempts/);
assert.match(added, /Newfoundland and Labrador \(NL\): 3 failed login attempts/);
assert.doesNotMatch(added, /remaining|Removing every/);
const removed = panel._regionConfirmation({ 'CA-NL': 3 }, true);
assert.doesNotMatch(removed, /Canada \(CA\)/);
assert.match(removed, /Newfoundland and Labrador/);
const empty = panel._regionConfirmation({}, false);
assert.match(empty, /Anywhere/);
assert.doesNotMatch(empty, /will be blocked|High impact/);
console.log('Region confirmation checks passed: add, narrow, and remove last.');

const listeners = {};
const shortcut = { dataset: { regionCode: 'CA-NL' }, addEventListener: (name, fn) => { listeners.shortcut = fn; } };
const form = {
  elements: { threshold: { value: '3', reportValidity: () => true }, region: { value: 'CA-NL' } },
  addEventListener: (name, fn) => { listeners.submit = fn; },
};
panel._data.settings = { public_region_enabled: true, public_region_rules: { CA: 5 } };
panel.shadowRoot = {
  getElementById: id => id === 'regional-threshold-form' ? form : null,
  querySelectorAll: selector => selector === 'button[data-region-code]' ? [shortcut] : [],
};
const posts = [];
panel._post = (action, payload) => posts.push({ action, payload });
panel._wireEvents();
listeners.shortcut();
assert.equal(posts.length, 0, 'Cancel must not save');
assert.match(message, /Canada \(CA\): 5/);
assert.match(message, /Newfoundland and Labrador \(NL\): 3/);
accepted = true;
listeners.shortcut();
assert.equal(posts[0].action, 'set_public_region');
assert.equal(posts[0].payload.value, 'CA-NL');
assert.equal(posts[0].payload.threshold, 3);
message = undefined;
panel._data.settings.public_region_enabled = false;
listeners.shortcut();
assert.equal(message, undefined, 'Saving a disabled rule must not claim to enable blocking');
console.log('Shortcut event checks passed: cancel, confirm, and disabled lock.');

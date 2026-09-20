const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let Panel;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../custom_components/ip_ban_manager/panel.js'), 'utf8'), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  window: {},
});

const panel = new Panel();
const markup = panel._gridMarkup(new Map([
  ['options-section', '<section class="options-section">options</section>'],
  ['allowed-ips-section', '<section class="allowed-ips-section">allowed</section>'],
  ['blocked-ips-section', '<section class="blocked-ips-section">blocked</section>'],
  ['allowed-regions-section', '<section class="allowed-regions-section">regions</section>'],
  ['blocked-networks-section', '<section class="blocked-networks-section">networks</section>'],
]));

const left = markup.slice(markup.indexOf('column-left'), markup.indexOf('column-right'));
const right = markup.slice(markup.indexOf('column-right'));

assert.match(left, /options-section[\s\S]*blocked-ips-section[\s\S]*blocked-networks-section/);
assert.doesNotMatch(left, /allowed-ips-section|allowed-regions-section/);
assert.match(right, /allowed-ips-section[\s\S]*allowed-regions-section/);
assert.equal(panel._sectionColumn('allowed-regions-section'), '.column-right');
assert.equal(panel._sectionColumn('blocked-networks-section'), '.column-left');

console.log('Panel cards use independent desktop columns with stable section placement.');

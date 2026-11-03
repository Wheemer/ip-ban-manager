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
const keys = ['activity-section','options-section','allowed-ips-section','blocked-ips-section','allowed-regions-section','blocked-networks-section','backup-section','notifications-section','silenced-section','npm-section','geoip-section'];
const markup = panel._gridMarkup(new Map(keys.map(key => [key, `<section class="${key}">${key}</section>`])));
for (const tab of ['activity','allowed','blocking','notifications','settings','backup']) {
  assert.match(markup, new RegExp(`aria-controls="page-${tab}"`));
  assert.match(markup, new RegExp(`id="page-${tab}" role="tabpanel" aria-labelledby="tab-${tab}"`));
}
const blocking = markup.slice(markup.indexOf('id="page-blocking"'),markup.indexOf('id="page-notifications"'));
assert.doesNotMatch(blocking, /options-section|backup-section|activity-section|allowed-ips-section/);
assert.match(blocking, /blocked-ips-section[\s\S]*blocked-networks-section[\s\S]*allowed-regions-section/);
assert.equal(panel._sectionColumn('activity-section'),'#page-activity');
assert.equal(panel._sectionColumn('backup-section'),'#page-backup');
assert.equal(panel._sectionColumn('allowed-regions-section'),'#page-blocking');
assert.equal(panel._sectionColumn('notifications-section'),'#page-notifications');
const buttons = ['activity','allowed','blocking','notifications','settings','backup'].map(id=>({dataset:{panelTab:id},setAttribute(key,value){this[key]=value;}}));
const pages = buttons.map(button=>({id:`page-${button.dataset.panelTab}`}));
panel.shadowRoot = {querySelectorAll: selector=>selector === '.tab-page' ? pages : buttons};
panel._selectPanelTab('settings');
assert.equal(panel._panelTab,'settings');
assert.equal(pages.filter(page=>!page.hidden).length,1);
assert.equal(pages.find(page=>!page.hidden).id,'page-settings');
assert.equal(buttons[4]['aria-selected'],'true');
assert.equal(buttons[4].tabIndex,0);
panel._selectPanelTab('invalid');
assert.equal(panel._panelTab,'settings');
console.log('Panel tabs: grouping, accessibility, selection, incremental destinations passed.');
for (const [action, style] of [['add_allowlist', 'primary'], ['add_blocked_network', 'blocking'], ['add_ban', 'blocking']]) {
  const section = panel._listSection('Title', 'Hint', [], 'remove', action, 'Address');
  assert.match(section, new RegExp(`<button class="${style}"`));
}
console.log('List commands distinguish trust additions from blocking actions.');
const applyButton = {};
const listeners = {};
let pending = false;
panel._bindApplyState(applyButton, [{addEventListener: (event, callback) => { listeners[event] = callback; }}], () => pending);
assert.equal(applyButton.disabled, true);
pending = true;
listeners.input();
assert.equal(applyButton.disabled, false);
pending = false;
listeners.change();
assert.equal(applyButton.disabled, true);
panel._busy = true;
pending = true;
listeners.change();
assert.equal(applyButton.disabled, true);
console.log('Apply requires unsaved changes and stays disabled while busy.');

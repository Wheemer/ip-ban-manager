const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let Panel;
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../custom_components/ip_ban_manager/panel.js'), 'utf8'), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  URLSearchParams,
  window: { setTimeout, clearTimeout },
});
(async () => {
  const panel = new Panel();
  panel._loaded = true;
  panel._isEditing = () => false;
  panel._renderSafely = () => {};
  let incremental = 0, scheduled = 0;
  panel._renderIncremental = () => { incremental++; };
  panel._scheduleLoad = () => { scheduled++; };
  const requests = [];
  panel._api = async (_, path) => {
    requests.push(path);
    return { enrichment_pending: requests.length === 1, translations: {} };
  };
  await panel._load();
  assert.match(requests[0], /initial=1/);
  assert.equal(scheduled, 1);
  assert.equal(panel._busy, false);
  await panel._load({ silent: true });
  assert.doesNotMatch(requests[1], /initial=/);
  assert.equal(incremental, 1);
  assert.equal(scheduled, 1);
  assert.match(panel._loadingMarkup(), /role="status"/);
  assert.match(panel._loadingMarkup(), /aria-hidden="true"/);
  console.log('Initial load: local-first payload, incremental enrichment, accessible skeleton passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

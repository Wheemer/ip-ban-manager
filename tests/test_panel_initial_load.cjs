const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let Panel;
const browser = {
  setTimeout, clearTimeout,
  location: new URL('https://ha.example/ip_ban_manager?tab=blocking'),
  history: {
    state: { navigation: true },
    replaceState(state, _, url) {
      assert.equal(state, this.state);
      browser.location = url;
    },
  },
};
vm.runInNewContext(fs.readFileSync(require('node:path').join(__dirname, '../custom_components/ip_ban_manager/panel.js'), 'utf8'), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  URL, URLSearchParams,
  window: browser,
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
  assert.equal(panel._handleInitialAction(), false);
  assert.equal(panel._panelTab, 'blocking');
  browser.location = new URL('https://ha.example/ip_ban_manager?action=silence_allowlisted_login&ip_address=192.168.1.1');
  let posts = 0;
  panel._post = async (action, payload) => {
    posts++;
    assert.equal(action, 'silence_allowlisted_login');
    assert.equal(payload.value, '192.168.1.1');
  };
  assert.equal(panel._handleInitialAction(), true);
  assert.equal(panel._panelTab, 'allowed');
  assert.equal(panel._handleInitialAction(), false);
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(browser.location.search, '?tab=allowed');
  assert.equal(posts, 1);
  panel._t = key => key;
  const available = panel._geoipStatus({
    geoip_database_present: true,
    geoip_update_available: true,
  });
  assert.match(available, /class="primary geoip-update-available" data-action="update_geoip"[^>]*>geoip.update_available/);
  const current = panel._geoipStatus({ geoip_database_present: true });
  assert.match(current, /class="secondary" data-action="update_geoip"[^>]*>geoip.update</);
  assert.match(current, /data-option="geoip_auto_update"/);
  console.log('Initial load: local-first payload, incremental enrichment, accessible skeleton passed.');
})().catch(error => { console.error(error); process.exitCode = 1; });

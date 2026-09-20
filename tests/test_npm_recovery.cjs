const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
let Panel;
vm.runInNewContext(fs.readFileSync(path.join(__dirname, '../custom_components/ip_ban_manager/panel.js'), 'utf8'), {
  HTMLElement: class {},
  customElements: { get: () => undefined, define: (_, value) => { Panel = value; } },
  window: { location: { hostname: 'ha.test' } },
});

(async () => {
  const panel = new Panel();
  panel._renderSafely = () => {};
  panel._showToast = () => {};
  panel._withTimeout = promise => promise;
  panel._language = () => 'en';
  panel._statusPath = () => 'status';
  panel._t = key => key;
  const connection = { configured: true, reauth_required: true, base_url: 'http://npm.test:81', identity: 'owner@test.invalid' };
  for (const [action, extra] of [['npm_disconnect', {}], ['set_options', { options: { npm_edge_protection_enabled: false } }]]) {
    const methods = [];
    panel._api = async method => {
      methods.push(method);
      if (method === 'POST') throw new Error('Token expired');
      return { npm: connection };
    };
    assert.equal(await panel._post(action, extra), false);
    assert.deepEqual(methods, ['POST', 'GET']);
    assert.equal(panel._data.npm.reauth_required, true);
    assert.match(panel._error, /Token expired/);
    assert.equal(panel._busy, false);
  }
  const form = panel._npmOptions(connection);
  assert.match(form, /npm-connect-form/);
  assert.match(form, /value="http:\/\/npm.test:81"/);
  assert.match(form, /value="owner@test.invalid"/);
  const configured = panel._npmOptions({
    configured: true,
    proxy_host_id: 4,
    protect_all_domains: true,
    hosts: [{ id: 4, domain_names: ['ha.test'] }],
  });
  assert.match(configured, /id="npm-protect-all-domains"[^>]*checked/);
  assert.match(configured, /npm\.protect_all_domains_hint/);
  assert.match(configured, /class="npm-disconnect-row"[\s\S]*id="npm-disconnect"/);
  assert.match(configured, /class="npm-action-row npm-action-row-apply"[\s\S]*id="npm-apply"/);
  const suggested = panel._npmOptions({ configured: false, suggested_url: 'http://192.168.2.66:81' });
  assert.match(suggested, /value="http:\/\/192.168.2.66:81"/);
  assert.doesNotMatch(suggested, /ha\.test/);
  assert.match(suggested, /form="npm-connect-form"/);
  const unchanged = { settings: { auto_ban_enabled: true } };
  assert.equal(
    panel._successMessage(
      'set_options',
      unchanged,
      unchanged,
      { options: { auto_ban_enabled: true } }
    ),
    'success.no_changes'
  );
  assert.equal(
    panel._successMessage(
      'add_allowlist',
      { settings: { ip_addresses: [] } },
      { settings: { ip_addresses: ['192.0.2.1'] } },
      { value: '192.0.2.1' }
    ),
    'add · allowed_ips.title · 192.0.2.1'
  );
  assert.equal(
    panel._successMessage(
      'set_options',
      { settings: { auto_ban_enabled: false } },
      { settings: { auto_ban_enabled: true } },
      { options: { auto_ban_enabled: true } }
    ),
    'Options applied. settings.auto_ban_enabled'
  );
  panel._api = async () => { throw new Error('Unavailable'); };
  assert.equal(await panel._post('npm_disconnect'), false);
  assert.match(panel._error, /Unavailable/);
  assert.equal(panel._busy, false);
  console.log('NPM recovery: failed actions refresh status, sign-in retains connection details, errors remain visible.');
})().catch(error => { console.error(error); process.exitCode = 1; });

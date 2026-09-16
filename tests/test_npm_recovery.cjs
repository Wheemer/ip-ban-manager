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
  panel._api = async () => { throw new Error('Unavailable'); };
  assert.equal(await panel._post('npm_disconnect'), false);
  assert.match(panel._error, /Unavailable/);
  assert.equal(panel._busy, false);
  console.log('NPM recovery: failed actions refresh status, sign-in retains connection details, errors remain visible.');
})().catch(error => { console.error(error); process.exitCode = 1; });

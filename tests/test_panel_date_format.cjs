const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

let Panel;
vm.runInNewContext(
  fs.readFileSync(
    path.join(__dirname, '../custom_components/ip_ban_manager/panel.js'),
    'utf8'
  ),
  {
    HTMLElement: class {},
    customElements: {
      get: () => undefined,
      define: (_, value) => {
        Panel = value;
      },
    },
    window: {},
  }
);

const panel = new Panel();
panel._hass = {
  locale: {
    language: 'en',
    date_format: 'DMY',
    time_format: '24',
    time_zone: 'UTC',
  },
};

assert.equal(panel._formatDate('2026-01-02T03:04:00+00:00'), '02/01/2026, 03:04');

panel._hass.locale.date_format = 'MDY';
assert.equal(panel._formatDate('2026-01-02T03:04:00+00:00'), '01/02/2026, 03:04');

panel._hass.locale.date_format = 'YMD';
assert.equal(panel._formatDate('2026-01-02T03:04:00+00:00'), '2026/01/02, 03:04');

console.log('Panel dates follow Home Assistant date_format and time settings.');

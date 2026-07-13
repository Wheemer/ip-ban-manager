class IPBanManagerPanel extends HTMLElement {
  set hass(hass) {
    this._hass = hass;
    const signature = this._stateSignature(hass);
    if (this._lastStateSignature && signature !== this._lastStateSignature) {
      this._scheduleLoad();
    }
    this._lastStateSignature = signature;

    if (!this._loaded) {
      this._loaded = true;
      if (!this._handleInitialAction()) {
        this._load();
      }
    }
  }

  connectedCallback() {
    this._renderShell();
    this._autoRefresh = window.setInterval(() => this._scheduleLoad(), 10000);
  }

  disconnectedCallback() {
    window.clearInterval(this._autoRefresh);
    window.clearTimeout(this._loadTimer);
  }

  async _api(method, path, data) {
    if (this._hass?.callApi) {
      return this._hass.callApi(method, path, data);
    }

    const response = await fetch(`/api/${path}`, {
      method,
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: data ? JSON.stringify(data) : undefined,
    });
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body?.ok === false) {
      throw new Error(body.error || body.message || response.statusText || `HTTP ${response.status}`);
    }
    return body;
  }

  async _load({ silent = false } = {}) {
    if (this._busy || this._loading || this._isEditing()) {
      return;
    }
    const canStayQuiet = silent && this._data;
    let loaded = false;
    this._loading = true;
    if (!canStayQuiet) {
      this._busy = true;
      this._error = "";
      this._render();
    }
    try {
      this._data = await this._api("GET", "ip_ban_manager/status");
      this._error = "";
      loaded = true;
    } catch (err) {
      if (!canStayQuiet) {
        this._error = this._errorMessage(err);
      }
    } finally {
      this._loading = false;
      this._busy = false;
      if (!canStayQuiet || loaded) {
        this._render();
      }
    }
  }

  _scheduleLoad() {
    if (!this._loaded || this._busy || this._isEditing()) {
      return;
    }
    window.clearTimeout(this._loadTimer);
    this._loadTimer = window.setTimeout(() => this._load({ silent: true }), 300);
  }

  async _post(action, extra = {}) {
    this._busy = true;
    this._error = "";
    this._notice = "";
    this._render();
    let ok = false;
    try {
      const result = await this._api("POST", "ip_ban_manager/manage", { action, ...extra });
      if (result?.status && result?.settings) {
        this._data = result;
      } else {
        this._data = await this._api("GET", "ip_ban_manager/status");
      }
      this._notice = this._successMessage(action);
      ok = true;
    } catch (err) {
      this._error = this._errorMessage(err);
    } finally {
      this._busy = false;
      this._render();
    }
    return ok;
  }

  _successMessage(action) {
    const path = this._data?.backup?.path || "/config/ip_ban_manager/ip-ban-manager-backup.yaml";
    if (action === "export_config") {
      return `Exported to ${path}`;
    }
    if (action === "import_config") {
      return `Imported ${path}`;
    }
    return "";
  }

  _handleInitialAction() {
    if (this._initialActionHandled) {
      return false;
    }
    this._initialActionHandled = true;

    const params = new URLSearchParams(window.location.search);
    const action = params.get("action");
    if (action !== "silence_allowlisted_login") {
      return false;
    }

    const ipAddress = params.get("ip_address");
    if (!ipAddress) {
      return false;
    }

    this._runInitialAction(action, {
      value: ipAddress,
      notification_id: params.get("notification_id") || undefined,
    });
    return true;
  }

  async _runInitialAction(action, payload) {
    await this._post(action, payload);
    window.history.replaceState(null, "", window.location.pathname);
  }

  _errorMessage(err) {
    if (typeof err === "string") {
      return err;
    }
    if (err?.body?.error) {
      return err.body.error;
    }
    if (err?.body?.message) {
      return err.body.message;
    }
    if (err?.message) {
      return err.message;
    }
    if (err?.error) {
      return err.error;
    }
    return "Request failed.";
  }

  _renderShell() {
    if (this.shadowRoot) {
      return;
    }
    this.attachShadow({ mode: "open" });
    this.shadowRoot.innerHTML = `
      <style>
        :host {
          display: block;
          min-height: 100vh;
          background: var(--primary-background-color);
          color: var(--primary-text-color);
          box-sizing: border-box;
          padding: 24px;
        }
        * { box-sizing: border-box; }
        .wrap { max-width: 1180px; margin: 0 auto; }
        header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 16px;
          margin-bottom: 20px;
        }
        .brand { display: flex; align-items: center; gap: 14px; min-width: 0; }
        .brand img { width: 44px; height: 44px; object-fit: contain; }
        h1 { margin: 0; font-size: 32px; line-height: 1.1; font-weight: 650; }
        button, input {
          font: inherit;
          color: inherit;
          border-radius: 6px;
        }
        button {
          border: 1px solid var(--divider-color);
          background: var(--card-background-color);
          padding: 8px 12px;
          cursor: pointer;
        }
        button.primary {
          border-color: var(--primary-color);
          background: var(--primary-color);
          color: var(--text-primary-color);
        }
        button.danger { color: var(--error-color); }
        button:disabled { opacity: .55; cursor: progress; }
        input {
          width: 100%;
          border: 1px solid var(--divider-color);
          background: var(--secondary-background-color);
          padding: 10px 12px;
        }
        .grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 16px;
        }
        section {
          background: var(--card-background-color);
          border: 1px solid var(--divider-color);
          border-radius: 8px;
          overflow: hidden;
        }
        section.wide { grid-column: 1 / -1; }
        h2 {
          margin: 0;
          padding: 16px;
          font-size: 18px;
          border-bottom: 1px solid var(--divider-color);
        }
        .body { padding: 16px; }
        .hint { color: var(--secondary-text-color); margin: 0 0 14px; }
        .health {
          margin-bottom: 14px;
          padding: 10px 12px;
          border-radius: 6px;
          font-size: 13px;
        }
        .health.warn {
          border: 1px solid var(--warning-color, #ffa600);
          background: rgba(255, 152, 0, 0.10);
        }
        .health ul {
          margin: 6px 0 0;
          padding-left: 18px;
        }
        .rows { display: grid; gap: 8px; margin-bottom: 14px; }
        .row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          align-items: center;
          gap: 10px;
          min-height: 42px;
          padding: 8px 10px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--secondary-background-color);
        }
        .row code { overflow-wrap: anywhere; white-space: normal; }
        .meta { color: var(--secondary-text-color); font-size: 13px; margin-top: 2px; }
        .empty {
          color: var(--secondary-text-color);
          padding: 12px;
          border: 1px dashed var(--divider-color);
          border-radius: 6px;
          margin-bottom: 14px;
        }
        form { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
        .options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
        .advanced-title {
          margin: 16px 0 8px;
          color: var(--secondary-text-color);
          font-weight: 600;
        }
        label.check { display: flex; align-items: flex-start; gap: 10px; }
        label.check input { width: auto; transform: scale(1.15); }
        label.check span { display: grid; gap: 2px; }
        label.check small {
          color: var(--secondary-text-color);
          font-size: 13px;
          line-height: 1.35;
        }
        label.check.risky {
          padding: 8px;
          border: 1px solid var(--warning-color, #ffa600);
          border-radius: 6px;
          background: rgba(255, 152, 0, 0.10);
        }
        .threshold {
          margin-top: 14px;
          max-width: 180px;
        }
        .geoip-status {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-top: 14px;
          padding: 10px 12px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--secondary-background-color);
        }
        .geoip-status div { display: grid; gap: 2px; }
        .geoip-status small {
          color: var(--secondary-text-color);
          font-size: 13px;
          line-height: 1.35;
        }
        .geoip-status a {
          color: var(--primary-color);
          text-decoration: none;
        }
        .geoip-status a:hover { text-decoration: underline; }
        .button-row { display: flex; gap: 8px; }
        .subsection {
          margin-top: 18px;
          padding: 12px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--secondary-background-color);
        }
        .subsection h3 {
          margin: 0 0 8px;
          font-size: 15px;
        }
        .actions {
          display: flex;
          justify-content: flex-end;
          margin-top: 16px;
        }
        .error {
          margin-bottom: 16px;
          padding: 12px 14px;
          border-radius: 6px;
          background: var(--error-color);
          color: var(--text-primary-color);
        }
        .notice {
          margin-bottom: 16px;
          padding: 12px 14px;
          border: 1px solid var(--success-color, #43a047);
          border-radius: 6px;
          color: var(--primary-text-color);
          background: rgba(67, 160, 71, 0.12);
        }
        @media (max-width: 760px) {
          :host { padding: 12px; }
          h1 { font-size: 26px; }
          .grid, .options { grid-template-columns: 1fr; }
          form { grid-template-columns: 1fr; }
          .threshold { max-width: none; }
          .geoip-status { align-items: flex-start; flex-direction: column; }
        }
      </style>
      <div class="wrap">
        <header>
          <div class="brand">
            <img src="/api/ip_ban_manager/icon.png" alt="">
            <h1>IP Ban Manager</h1>
          </div>
        </header>
        <div id="content"></div>
      </div>
    `;
    this._render();
  }

  _render() {
    if (!this.shadowRoot) {
      return;
    }
    const content = this.shadowRoot.getElementById("content");
    if (!content) {
      return;
    }
    if (this._busy && !this._data) {
      content.innerHTML = `<section><div class="body">Loading...</div></section>`;
      return;
    }
    if (!this._data) {
      content.innerHTML = this._error ? `<div class="error">${this._escape(this._error)}</div>` : "";
      return;
    }

    const status = this._data.status;
    const settings = this._data.settings;
    content.innerHTML = `
      ${this._error ? `<div class="error">${this._escape(this._error)}</div>` : ""}
      ${this._notice ? `<div class="notice">${this._escape(this._notice)}</div>` : ""}
      <div class="grid">
        ${this._optionsSection(settings)}
        ${this._listSection("Allowed IPs", "Trusted IPv4/IPv6 addresses and networks. These entries win over exact bans, blocked networks, and default-deny mode. IPv4 wildcards like 192.168.1.* are supported.", settings.ip_addresses, "remove_allowlist", "add_allowlist", "IPv4/IPv6 address, CIDR, or IPv4 wildcard", this._silencedAllowlistedLogins(settings))}
        ${this._banSection(status.banned_ips)}
        ${this._listSection("Blocked Networks", "Managed IPv4/IPv6 CIDR or IPv4 wildcard networks, enforced without writing ranges into ip_bans.yaml.", settings.blocked_networks, "remove_blocked_network", "add_blocked_network", "CIDR or IPv4 wildcard network")}
      </div>
    `;
    this._wireEvents();
  }

  _listSection(title, hint, rows, removeAction, addAction, placeholder, extra = "") {
    return `
      <section>
        <h2>${title}</h2>
        <div class="body">
          <p class="hint">${hint}</p>
          ${this._rows(rows, removeAction)}
          <form data-action="${addAction}">
            <input name="value" placeholder="${placeholder}" autocomplete="off">
            <button class="primary" ${this._busy ? "disabled" : ""}>Add</button>
          </form>
          ${extra}
        </div>
      </section>
    `;
  }

  _banSection(bans) {
    const rows = bans.map((ban) => ({
      label: ban.ip_address,
      detail: [this._formatDate(ban.banned_at), ban.location].filter(Boolean).join(" - "),
      value: ban.ip_address,
    }));
    return `
      <section>
        <h2>Blocked IPs</h2>
        <div class="body">
          <p class="hint">Home Assistant's native exact IPv4/IPv6 block list, written oldest first in ip_bans.yaml.</p>
          ${this._rows(rows, "remove_ban")}
          <form data-action="add_ban">
            <input name="value" placeholder="IPv4/IPv6 address" autocomplete="off">
            <button class="primary" ${this._busy ? "disabled" : ""}>Block</button>
          </form>
        </div>
      </section>
    `;
  }

  _optionsSection(settings) {
    const geoip = this._data.geoip || {};
    const backup = this._data.backup || {};
    return `
      <section>
        <h2>Options</h2>
        <div class="body">
          ${this._healthSummary(this._data.status.health)}
          <div class="options">
            ${this._checkbox("auto_ban_enabled", "Automatic bans", "Block failed login sources.", settings.auto_ban_enabled)}
            ${this._checkbox("ban_notifications_enabled", "Automatic ban notifications", "Show alerts when IPs are blocked.", settings.ban_notifications_enabled)}
            ${this._checkbox("allowlisted_login_notifications_enabled", "Allowlisted login notifications", "Alert on failed trusted logins.", settings.allowlisted_login_notifications_enabled)}
            ${this._checkbox("sidebar_panel_enabled", "Show in sidebar", "Add the left menu page.", settings.sidebar_panel_enabled)}
            ${this._checkbox("geoip_enabled", "GeoIP location labels", "Show approximate public-IP locations. If the local database is missing, Apply downloads it.", settings.geoip_enabled)}
          </div>
          ${this._geoipStatus(geoip)}
          ${this._backupStatus(backup)}
          <div class="advanced-title">Advanced</div>
          <div class="options">
            ${this._checkbox("allowlisted_logins_can_ban", "Bans inside Allowed IPs", "Be careful: trusted IPs can be blocked.", settings.allowlisted_logins_can_ban, true)}
            ${this._checkbox("default_deny_enabled", "Block everything outside Allowed IPs", "Be careful: only Allowed IPs can connect.", settings.default_deny_enabled, true)}
          </div>
          <div class="threshold">
            <label>
              <p class="hint">Login attempts threshold</p>
              <input id="threshold" type="number" min="0" max="100" value="${Number(settings.login_attempts_threshold || 0)}">
            </label>
          </div>
          <div class="actions">
            <button class="primary" id="save-options" ${this._busy ? "disabled" : ""}>Apply</button>
          </div>
        </div>
      </section>
    `;
  }

  _backupStatus(backup) {
    const updated = backup.last_export ? this._formatDate(backup.last_export) : "No export yet";
    return `
      <div class="geoip-status">
        <div>
          <strong>Backup</strong>
          <small>${this._escape(backup.path || "/config/ip_ban_manager/ip-ban-manager-backup.yaml")}</small>
          <small>${backup.exists ? `Last export: ${this._escape(updated)}` : "Export creates the file; import restores it manually."}</small>
        </div>
        <div class="button-row">
          <button data-action="export_config" ${this._busy ? "disabled" : ""}>Export</button>
          <button
            data-action="import_config"
            data-confirm="Import ip-ban-manager-backup.yaml and replace current IP Ban Manager settings and exact IP bans?"
            ${this._busy || !backup.exists ? "disabled" : ""}
          >Import</button>
        </div>
      </div>
    `;
  }

  _geoipStatus(geoip) {
    const installed = Boolean(geoip.geoip_database_present);
    const updated = geoip.geoip_database_updated ? this._formatDate(geoip.geoip_database_updated) : "Not installed";
    const status = installed ? `Installed - ${this._escape(updated)}` : "Enable GeoIP and Apply to download";
    return `
      <div class="geoip-status">
        <div>
          <strong>GeoIP database</strong>
          <small>${status}</small>
          <small>Location data: <a href="https://db-ip.com" target="_blank" rel="noreferrer">DB-IP City Lite</a></small>
        </div>
        ${installed ? `<button data-action="update_geoip" ${this._busy ? "disabled" : ""}>Update</button>` : ""}
      </div>
    `;
  }

  _healthSummary(health) {
    if (!health) {
      return "";
    }
    const issues = health.health_issues || [];
    if (!issues.length) {
      return "";
    }
    return `
      <div class="health warn">
        <strong>Health check needs attention</strong>
        <ul>${issues.map((issue) => `<li>${this._escape(issue)}</li>`).join("")}</ul>
      </div>
    `;
  }

  _silencedAllowlistedLogins(settings) {
    const silencedRows = this._rows(
      settings.silenced_allowlisted_login_ips || [],
      "unsilence_allowlisted_login"
    );
    return `
      <div class="subsection">
        <h3>Silenced allowlisted-login notifications</h3>
        <p class="hint">Addresses silenced from allowlisted-login alerts.</p>
        ${silencedRows}
      </div>
    `;
  }

  _checkbox(key, label, description, checked, risky = false) {
    return `
      <label class="check ${risky ? "risky" : ""}">
        <input type="checkbox" data-option="${key}" ${checked ? "checked" : ""}>
        <span>${label}<small>${description}</small></span>
      </label>
    `;
  }

  _rows(rows, removeAction) {
    const normalized = rows.map((row) =>
      typeof row === "string" ? { label: row, value: row } : row
    );
    if (!normalized.length) {
      return `<div class="empty">None</div>`;
    }
    return `
      <div class="rows">
        ${normalized.map((row) => `
          <div class="row">
            <div>
              <code>${this._escape(row.label)}</code>
              ${row.detail ? `<div class="meta">${this._escape(row.detail)}</div>` : ""}
            </div>
            <button class="danger" data-action="${removeAction}" data-value="${this._escape(row.value)}" ${this._busy ? "disabled" : ""}>Remove</button>
          </div>
        `).join("")}
      </div>
    `;
  }

  _wireEvents() {
    this.shadowRoot.querySelectorAll("form[data-action]").forEach((form) => {
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const value = new FormData(form).get("value");
        if (value) {
          this._post(form.dataset.action, { value }).then((ok) => {
            if (ok) {
              form.reset();
            }
          });
        }
      });
    });
    this.shadowRoot.querySelectorAll("button[data-action]").forEach((button) => {
      button.addEventListener("click", () => {
        if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) {
          return;
        }
        this._post(button.dataset.action, { value: button.dataset.value });
      });
    });
    const saveOptions = this.shadowRoot.getElementById("save-options");
    if (saveOptions) {
      saveOptions.addEventListener("click", () => {
        const options = {};
        this.shadowRoot.querySelectorAll("input[data-option]").forEach((input) => {
          options[input.dataset.option] = input.checked;
        });
        options.login_attempts_threshold = Number(
          this.shadowRoot.getElementById("threshold").value || 0
        );
        this._post("set_options", { options });
      });
    }
  }

  _formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return value;
    }
    return date.toLocaleString(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  _isEditing() {
    const active = this.shadowRoot?.activeElement;
    return active?.tagName === "INPUT";
  }

  _stateSignature(hass) {
    if (!hass?.states) {
      return "";
    }
    return Object.entries(hass.states)
      .filter(([entityId]) => entityId.startsWith("sensor.ip_ban_manager_"))
      .map(([entityId, state]) => `${entityId}:${state.state}:${state.last_changed}`)
      .sort()
      .join("|");
  }

  _escape(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[char]);
  }
}

customElements.define("ip-ban-manager-panel-v19", IPBanManagerPanel);

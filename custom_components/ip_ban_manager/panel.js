const PANEL_VERSION = "__VERSION__";
class IPBanManagerPanel extends HTMLElement {
  set hass(hass) {
    const previousLocale = this._localeSignature(this._hass);
    this._hass = hass;
    const signature = this._stateSignature(hass);
    if (this._lastStateSignature && signature !== this._lastStateSignature) {
      this._scheduleLoad();
    }
    this._lastStateSignature = signature;

    const nextLocale = this._localeSignature(hass);
    if (
      this._loaded &&
      previousLocale &&
      nextLocale !== previousLocale
    ) {
      this._scheduleLoad();
      if (this._data) {
        this._renderSafely();
      }
    }

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

  _language() {
    return this._hass?.locale?.language || this._data?.language || "en";
  }

  _localeSignature(hass) {
    if (!hass?.locale) {
      return "";
    }
    const { language, time_zone, time_format, date_format } = hass.locale;
    return [language, time_zone, time_format, date_format].join("|");
  }

  _t(key, vars = {}) {
    const translations = this._data?.translations || {};
    let text = translations[key] || key;
    for (const [name, value] of Object.entries(vars)) {
      text = text.replaceAll(`{${name}}`, String(value));
    }
    return text;
  }

  _statusPath() {
    const params = new URLSearchParams({ language: this._language() });
    return `ip_ban_manager/status?${params.toString()}`;
  }

  _requestFailedMessage() {
    return this._data?.translations?.request_failed || "Request failed.";
  }

  _withTimeout(promise, timeoutMs = 30000) {
    return Promise.race([
      promise,
      new Promise((_, reject) => {
        window.setTimeout(
          () => reject(new Error(this._requestFailedMessage())),
          timeoutMs
        );
      }),
    ]);
  }

  async _readApiResponse(response) {
    const body = await response.json().catch(() => ({}));
    if (!response.ok || body?.ok === false) {
      throw new Error(
        body.error || body.message || response.statusText || `HTTP ${response.status}`
      );
    }
    return body;
  }

  async _api(method, path, data) {
    const url = `/api/${path}`;
    const init = {
      method,
      headers: { "Content-Type": "application/json" },
    };
    if (data !== undefined) {
      init.body = JSON.stringify(data);
    }

    if (this._hass?.fetchWithAuth) {
      const response = await this._hass.fetchWithAuth(url, init);
      return this._readApiResponse(response);
    }

    if (this._hass?.callApi) {
      const result =
        method === "GET"
          ? await this._hass.callApi(method, path)
          : await this._hass.callApi(method, path, data);
      if (result?.ok === false) {
        throw new Error(result.error || result.message || this._requestFailedMessage());
      }
      return result;
    }

    const response = await fetch(url, {
      ...init,
      credentials: "same-origin",
    });
    return this._readApiResponse(response);
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
      this._renderSafely();
    }
    try {
      this._data = await this._withTimeout(this._api("GET", this._statusPath()));
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
        this._renderSafely();
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
    this._renderSafely();
    let ok = false;
    try {
      const result = await this._withTimeout(
        this._api("POST", "ip_ban_manager/manage", {
          action,
          language: this._language(),
          ...extra,
        })
      );
      if (result?.status && result?.settings) {
        this._data = result;
      } else {
        this._data = await this._withTimeout(this._api("GET", this._statusPath()));
      }
      if (action === "download_config" && result?.download?.content) {
        this._triggerBrowserDownload(result.download);
      }
      this._notice = this._successMessage(action);
      ok = true;
    } catch (err) {
      this._error = this._errorMessage(err);
    } finally {
      this._busy = false;
      this._renderSafely();
    }
    return ok;
  }

  _triggerBrowserDownload(download) {
    const blob = new Blob([download.content], { type: "application/yaml" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = download.filename || "ip-ban-manager-backup.yaml";
    link.click();
    URL.revokeObjectURL(url);
  }

  _successMessage(action) {
    const path = this._data?.backup?.path || "/config/ip_ban_manager/ip-ban-manager-backup.yaml";
    const key = `success.${action}`;
    if (this._data?.translations?.[key]) {
      return this._t(key, { path });
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
    try {
      await this._post(action, payload);
    } finally {
      window.history.replaceState(null, "", window.location.pathname);
      if (!this._data) {
        await this._load();
      }
    }
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
    return this._data?.translations?.request_failed || "Request failed.";
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
        .brand-title {
          display: flex;
          align-items: baseline;
          gap: 10px;
          flex-wrap: wrap;
          min-width: 0;
        }
        h1 { margin: 0; font-size: 32px; line-height: 1.1; font-weight: 650; }
        .version {
          color: var(--secondary-text-color);
          font-size: 14px;
          font-weight: 500;
          line-height: 1.1;
          white-space: nowrap;
        }
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
        .backup-stack {
          display: grid;
          gap: 14px;
          margin-top: 14px;
        }
        .backup-stack .geoip-status { margin-top: 0; }
        .button-row { display: flex; gap: 8px; flex-wrap: wrap; }
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
          margin-bottom: 20px;
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
            <div class="brand-title">
              <h1>IP Ban Manager</h1>
              <span class="version" id="version">v__VERSION__</span>
            </div>
          </div>
        </header>
        <div id="content"></div>
      </div>
    `;
    this._renderSafely();
  }

  _updateVersionLabel() {
    const versionEl = this.shadowRoot?.getElementById("version");
    if (!versionEl) {
      return;
    }
    const version = this._data?.version || PANEL_VERSION;
    versionEl.textContent = version ? `v${version}` : "";
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
      this._updateChromeLabels();
      return;
    }
    if (!this._data) {
      content.innerHTML = this._error ? `<div class="error">${this._escape(this._error)}</div>` : "";
      return;
    }

    const status = this._data.status || {};
    const settings = this._data.settings || {};
    this._updateChromeLabels();
    content.innerHTML = `
      ${this._error ? `<div class="error">${this._escape(this._error)}</div>` : ""}
      ${this._notice ? `<div class="notice">${this._escape(this._notice)}</div>` : ""}
      <div class="grid">
        ${this._optionsSection(settings)}
        ${this._listSection(this._t("allowed_ips.title"), this._t("allowed_ips.hint"), this._allowlistRows(settings), "remove_allowlist", "add_allowlist", this._t("allowed_ips.placeholder"), this._silencedAllowlistedLogins(settings), this._riskyAllowlistRemoveConfirm(settings))}
        ${this._banSection(status.banned_ips || [])}
        ${this._listSection(this._t("blocked_networks.title"), this._t("blocked_networks.hint"), this._networkRows(settings.blocked_network_entries || settings.blocked_networks || []), "remove_blocked_network", "add_blocked_network", this._t("blocked_networks.placeholder"))}
      </div>
    `;
    this._wireEvents();
  }

  _renderSafely() {
    try {
      this._render();
    } catch (err) {
      this._busy = false;
      this._loading = false;
      this._error = this._errorMessage(err);
      const content = this.shadowRoot?.getElementById("content");
      if (content) {
        content.innerHTML = `<div class="error">${this._escape(this._error)}</div>`;
        this._updateChromeLabels();
      }
      console.error("IP Ban Manager panel render failed", err);
    }
  }

  _updateChromeLabels() {
    const titleEl = this.shadowRoot?.querySelector("h1");
    if (titleEl) {
      titleEl.textContent = this._data?.translations?.title || "IP Ban Manager";
    }
    this._updateVersionLabel();
  }

  _riskyAllowlistRemoveConfirm(settings) {
    if (!settings.default_deny_enabled && !(settings.blocked_networks || []).length) {
      return "";
    }
    return this._t("allowed_ips.remove_confirm");
  }

  _allowlistRows(settings) {
    const entries = settings.ip_addresses || settings.allowlist_entries || [];
    return entries.map((entry) => {
      const network = typeof entry === "string" ? entry : entry.network;
      return { label: network, value: network };
    });
  }

  _networkRows(entries) {
    return (entries || []).map((entry) => {
      if (typeof entry === "string") {
        return {
          label: entry,
          value: entry,
          detail: this._t("added_before_tracking"),
        };
      }
      return {
        label: entry.network,
        value: entry.network,
        detail: this._entryDetail(entry),
      };
    });
  }

  _entryDetail(entry) {
    const parts = [];
    if (entry.added_at) {
      parts.push(this._formatDate(entry.added_at));
    }
    if (entry.source) {
      parts.push(this._sourceLabel(entry.source));
    } else if (!entry.added_at) {
      parts.push(this._t("added_before_tracking"));
    }
    return parts.join(" · ");
  }

  _sourceLabel(source) {
    const key = `sources.${source}`;
    const translated = this._t(key);
    return translated === key ? source : translated;
  }

  _listSection(title, hint, rows, removeAction, addAction, placeholder, extra = "", removeConfirm = "") {
    const addLabel = addAction === "add_ban" ? this._t("block") : this._t("add");
    return `
      <section>
        <h2>${title}</h2>
        <div class="body">
          <p class="hint">${hint}</p>
          ${this._rows(rows, removeAction, removeConfirm)}
          <form data-action="${addAction}">
            <input name="value" placeholder="${placeholder}" autocomplete="off">
            <button class="primary" ${this._busy ? "disabled" : ""}>${addLabel}</button>
          </form>
          ${extra}
        </div>
      </section>
    `;
  }

  _banSection(bans) {
    const rows = (bans || []).map((ban) => ({
      label: ban.ip_address,
      detail: [this._formatDate(ban.banned_at), ban.location].filter(Boolean).join(" · "),
      value: ban.ip_address,
    }));
    return `
      <section>
        <h2>${this._t("blocked_ips.title")}</h2>
        <div class="body">
          <p class="hint">${this._t("blocked_ips.hint")}</p>
          ${this._rows(rows, "remove_ban")}
          <form data-action="add_ban">
            <input name="value" placeholder="${this._t("blocked_ips.placeholder")}" autocomplete="off">
            <button class="primary" ${this._busy ? "disabled" : ""}>${this._t("block")}</button>
          </form>
        </div>
      </section>
    `;
  }

  _optionsSection(settings) {
    const geoip = this._data?.geoip || {};
    const backup = this._data?.backup || {};
    return `
      <section>
        <h2>${this._t("options")}</h2>
        <div class="body">
          ${this._healthSummary(this._data?.status?.health)}
          <div class="options">
            ${this._checkbox("auto_ban_enabled", this._t("settings.auto_ban_enabled"), this._t("settings.auto_ban_enabled_hint"), settings.auto_ban_enabled)}
            ${this._checkbox("ban_notifications_enabled", this._t("settings.ban_notifications_enabled"), this._t("settings.ban_notifications_enabled_hint"), settings.ban_notifications_enabled)}
            ${this._checkbox("allowlisted_login_notifications_enabled", this._t("settings.allowlisted_login_notifications_enabled"), this._t("settings.allowlisted_login_notifications_enabled_hint"), settings.allowlisted_login_notifications_enabled)}
            ${this._checkbox("sidebar_panel_enabled", this._t("settings.sidebar_panel_enabled"), this._t("settings.sidebar_panel_enabled_hint"), settings.sidebar_panel_enabled)}
            ${this._checkbox("geoip_enabled", this._t("settings.geoip_enabled"), this._t("settings.geoip_enabled_hint"), settings.geoip_enabled)}
          </div>
          <div class="threshold">
            <label>
              <p class="hint">${this._t("settings.login_attempts_threshold")}</p>
              <input id="threshold" type="number" min="0" max="100" value="${Number(settings.login_attempts_threshold || 0)}">
            </label>
          </div>
          <div class="advanced-title">${this._t("advanced")}</div>
          <div class="options">
            ${this._checkbox("allowlisted_logins_can_ban", this._t("settings.allowlisted_logins_can_ban"), this._t("settings.allowlisted_logins_can_ban_hint"), settings.allowlisted_logins_can_ban, true)}
            ${this._checkbox("default_deny_enabled", this._t("settings.default_deny_enabled"), this._t("settings.default_deny_enabled_hint"), settings.default_deny_enabled, true)}
          </div>
          <div class="actions">
            <button class="primary" id="save-options" ${this._busy ? "disabled" : ""}>${this._t("apply")}</button>
          </div>
          ${this._geoipStatus(geoip)}
          ${this._backupStatus(backup)}
        </div>
      </section>
    `;
  }

  _backupStatus(backup) {
    const updated = backup.last_export ? this._formatDate(backup.last_export) : "";
    return `
      <div class="backup-stack">
        <div class="geoip-status">
          <div>
            <strong>${this._t("backup.save_title")}</strong>
            <small>${this._escape(backup.path || "/config/ip_ban_manager/ip-ban-manager-backup.yaml")}</small>
            <small>${backup.exists ? this._escape(this._t("backup.save_last", { date: updated })) : this._t("backup.save_none")}</small>
          </div>
          <div class="button-row">
            <button data-action="export_config" ${this._busy ? "disabled" : ""}>${this._t("backup.save")}</button>
            <button
              data-action="import_config"
              data-confirm="${this._escape(this._t("backup.restore_confirm"))}"
              ${this._busy || !backup.exists ? "disabled" : ""}
            >${this._t("backup.restore")}</button>
          </div>
        </div>
        <div class="geoip-status">
          <div>
            <strong>${this._t("backup.transfer_title")}</strong>
            <small>${this._t("backup.transfer_hint")}</small>
          </div>
          <div class="button-row">
            <button data-action="download_config" ${this._busy ? "disabled" : ""}>${this._t("backup.download")}</button>
            <button data-action="upload_config" ${this._busy ? "disabled" : ""}>${this._t("backup.upload")}</button>
            <input id="backup-upload" type="file" accept=".yaml,.yml,text/yaml,text/plain" hidden>
          </div>
        </div>
      </div>
    `;
  }

  _geoipStatus(geoip) {
    const installed = Boolean(geoip.geoip_database_present);
    const updated = geoip.geoip_database_updated ? this._formatDate(geoip.geoip_database_updated) : this._t("geoip.not_installed");
    const status = installed ? this._t("geoip.installed", { date: updated }) : this._t("geoip.download_hint");
    return `
      <div class="geoip-status">
        <div>
          <strong>${this._t("geoip.title")}</strong>
          <small>${status}</small>
          <small>${this._t("geoip.attribution")} <a href="https://db-ip.com" target="_blank" rel="noreferrer">DB-IP City Lite</a></small>
        </div>
        ${installed ? `<button data-action="update_geoip" ${this._busy ? "disabled" : ""}>${this._t("geoip.update")}</button>` : ""}
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
        <strong>${this._t("health.title")}</strong>
        <ul>${issues.map((issue) => {
          if (typeof issue === "string") {
            return `<li>${this._escape(issue)}</li>`;
          }
          const key = `health.issues.${issue.key}`;
          const text = this._t(key, issue.placeholders || {});
          return `<li>${this._escape(text === key ? issue.key : text)}</li>`;
        }).join("")}</ul>
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
        <h3>${this._t("silenced_logins.title")}</h3>
        <p class="hint">${this._t("silenced_logins.hint")}</p>
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

  _rows(rows, removeAction, removeConfirm = "") {
    const normalized = (rows || []).map((row) =>
      typeof row === "string" ? { label: row, value: row } : row
    );
    if (!normalized.length) {
      return `<div class="empty">${this._escape(this._t("none"))}</div>`;
    }
    const confirmAttr = removeConfirm
      ? ` data-confirm="${this._escape(removeConfirm)}"`
      : "";
    return `
      <div class="rows">
        ${normalized.map((row) => `
          <div class="row">
            <div>
              <code>${this._escape(row.label)}</code>
              ${row.detail ? `<div class="meta">${this._escape(row.detail)}</div>` : ""}
            </div>
            <button class="danger" data-action="${removeAction}" data-value="${this._escape(row.value)}"${confirmAttr} ${this._busy ? "disabled" : ""}>${this._t("remove")}</button>
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
        if (button.dataset.action === "upload_config") {
          const upload = this.shadowRoot.getElementById("backup-upload");
          if (upload) {
            upload.click();
          }
          return;
        }
        if (button.dataset.confirm && !window.confirm(button.dataset.confirm)) {
          return;
        }
        this._post(button.dataset.action, { value: button.dataset.value });
      });
    });
    const uploadInput = this.shadowRoot.getElementById("backup-upload");
    if (uploadInput) {
      uploadInput.addEventListener("change", async () => {
        const file = uploadInput.files && uploadInput.files[0];
        uploadInput.value = "";
        if (!file) {
          return;
        }
        if (
          !window.confirm(
            this._t("backup.upload_confirm", { filename: file.name })
          )
        ) {
          return;
        }
        try {
          const content = await file.text();
          await this._post("upload_config", { content });
        } catch (err) {
          this._error = this._errorMessage(err);
          this._renderSafely();
        }
      });
    }
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

  _resolveTimeZone() {
    const candidate =
      this._hass?.locale?.time_zone || this._hass?.config?.time_zone || "";
    if (typeof candidate !== "string") {
      return undefined;
    }
    const normalized = candidate.trim();
    if (!normalized) {
      return undefined;
    }
    const lower = normalized.toLowerCase();
    if (lower === "local" || lower === "server") {
      return undefined;
    }
    try {
      Intl.DateTimeFormat(undefined, { timeZone: normalized });
      return normalized;
    } catch {
      return undefined;
    }
  }

  _formatDate(value) {
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
      return String(value);
    }
    const locale = this._hass?.locale?.language;
    const timeZone = this._resolveTimeZone();
    const options = {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    };
    if (timeZone) {
      options.timeZone = timeZone;
    }
    const timeFormat = this._hass?.locale?.time_format;
    if (timeFormat === "24") {
      options.hour12 = false;
    } else if (timeFormat === "12") {
      options.hour12 = true;
    }
    try {
      return new Intl.DateTimeFormat(locale, options).format(date);
    } catch (err) {
      try {
        return date.toLocaleString(locale, timeZone ? { timeZone } : undefined);
      } catch {
        return String(value);
      }
    }
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

if (!customElements.get("ip-ban-manager-panel")) {
  customElements.define("ip-ban-manager-panel", IPBanManagerPanel);
}

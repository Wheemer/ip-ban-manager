const PANEL_VERSION = "__VERSION__";
const PANEL_VERSION_PLACEHOLDER = "__" + "VERSION__";
const TOAST_SUCCESS_DURATION_MS = 3500;
const TOAST_ERROR_DURATION_MS = 6500;
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
    window.clearTimeout(this._toastTimer);
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
    this._toast = "";
    this._toastType = "success";
    if (action === "update_geoip") {
      this._showToast(this._t("geoip.downloading"), "success", false);
    }
    this._renderSafely();
    let ok = false;
    try {
      const timeoutMs = action === "update_geoip" ? 120000 : 30000;
      const result = await this._withTimeout(
        this._api("POST", "ip_ban_manager/manage", {
          action,
          language: this._language(),
          ...extra,
        }),
        timeoutMs
      );
      if (result?.status && result?.settings) {
        this._data = result;
      } else {
        this._data = await this._withTimeout(this._api("GET", this._statusPath()));
      }
      if (action === "download_config" && result?.download?.content) {
        this._triggerBrowserDownload(result.download);
      }
      this._showToast(this._successMessage(action), "success");
      ok = true;
    } catch (err) {
      this._error = this._errorMessage(err);
      this._showToast(this._error, "error");
    } finally {
      this._busy = false;
      this._renderSafely();
    }
    return ok;
  }

  _showToast(message, type = "success", autoHide = true) {
    this._toast = message || "";
    this._toastType = type;
    this._renderToast();
    if (autoHide) {
      this._scheduleToastClear();
    } else {
      window.clearTimeout(this._toastTimer);
    }
  }

  _scheduleToastClear() {
    window.clearTimeout(this._toastTimer);
    if (!this._toast) {
      return;
    }
    const duration =
      this._toastType === "error" ? TOAST_ERROR_DURATION_MS : TOAST_SUCCESS_DURATION_MS;
    this._toastTimer = window.setTimeout(() => {
      this._toast = "";
      this._renderToast();
    }, duration);
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
    const fallbacks = {
      set_options: "Options applied.",
      update_geoip: "GeoIP database updated.",
      export_config: `Saved backup to ${path}`,
      import_config: `Restored backup from ${path}`,
      download_config: "Backup downloaded.",
      upload_config: "Backup uploaded and applied.",
    };
    const key = `success.${action}`;
    if (this._data?.translations?.[key]) {
      return this._t(key, { path });
    }
    return fallbacks[action] || "Done.";
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
          grid-template-areas:
            "options allowed"
            "blocked regions"
            "networks regions";
          gap: 16px;
          align-items: start;
        }
        .options-section { grid-area: options; }
        .allowed-ips-section { grid-area: allowed; }
        .allowed-regions-section { grid-area: regions; }
        .blocked-ips-section { grid-area: blocked; }
        .blocked-networks-section { grid-area: networks; }
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
        .policy-choices {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 8px;
          margin-top: 12px;
        }
        .policy-card {
          display: grid;
          grid-template-columns: auto minmax(0, 1fr);
          gap: 8px;
          align-items: center;
          min-height: 58px;
          padding: 8px 10px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--secondary-background-color);
        }
        .policy-card.custom { grid-column: 1 / -1; }
        .policy-card input[type="radio"] {
          width: auto;
          margin: 0;
          transform: scale(1.05);
        }
        .policy-card strong { display: block; margin-bottom: 2px; }
        .policy-card small {
          display: block;
          color: var(--secondary-text-color);
          font-size: 12px;
          line-height: 1.25;
        }
        .policy-card:has(input:checked) {
          border-color: var(--primary-color);
          background: color-mix(in srgb, var(--primary-color) 12%, var(--secondary-background-color));
        }
        .allowed-regions-section {
          border-color: var(--warning-color, #ffa600);
        }
        .allowed-region-warning {
          margin: 0 0 12px;
          padding: 10px 12px;
          border: 1px solid var(--warning-color, #ffa600);
          border-radius: 6px;
          background: color-mix(in srgb, var(--warning-color, #ffa600) 14%, var(--card-background-color));
          color: var(--primary-text-color);
          font-size: 13px;
          line-height: 1.35;
        }
        .policy-card.restrictive {
          border-color: color-mix(in srgb, var(--warning-color, #ffa600) 70%, var(--divider-color));
        }
        .policy-card.restrictive:has(input:checked) {
          border-color: var(--warning-color, #ffa600);
          background: color-mix(in srgb, var(--warning-color, #ffa600) 18%, var(--secondary-background-color));
        }
        .custom-region-fields {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 8px;
          margin-top: 10px;
        }
        .policy-card.custom:not(:has(input:checked)) .custom-region-fields {
          display: none;
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
        .toast {
          position: fixed;
          right: 24px;
          bottom: 24px;
          z-index: 10;
          display: flex;
          align-items: center;
          gap: 10px;
          max-width: min(380px, calc(100vw - 48px));
          min-height: 44px;
          padding: 12px 14px 12px 12px;
          border: 1px solid color-mix(in srgb, var(--success-color, #43a047) 70%, var(--divider-color));
          border-left: 4px solid var(--success-color, #43a047);
          border-radius: 6px;
          box-shadow: 0 12px 34px rgba(0, 0, 0, 0.34);
          color: var(--primary-text-color);
          background: color-mix(
            in srgb,
            var(--success-color, #43a047) 24%,
            var(--card-background-color)
          );
          font-size: 14px;
          font-weight: 600;
          line-height: 1.35;
          animation: toast-in 140ms ease-out;
        }
        .toast[hidden] {
          display: none;
        }
        .toast::before {
          content: "";
          display: grid;
          place-items: center;
          flex: 0 0 auto;
          width: 10px;
          height: 10px;
          border-radius: 50%;
          background: var(--success-color, #43a047);
          box-shadow: 0 0 0 4px color-mix(in srgb, var(--success-color, #43a047) 18%, transparent);
        }
        .toast.error {
          border-color: color-mix(in srgb, var(--error-color) 70%, var(--divider-color));
          border-left-color: var(--error-color);
          background: color-mix(
            in srgb,
            var(--error-color) 24%,
            var(--card-background-color)
          );
        }
        .toast.error::before {
          content: "!";
          width: 18px;
          height: 18px;
          background: var(--error-color);
          box-shadow: 0 0 0 4px color-mix(in srgb, var(--error-color) 20%, transparent);
          color: var(--text-primary-color, #ffffff);
          font-size: 13px;
          font-weight: 800;
          line-height: 1;
        }
        @keyframes toast-in {
          from {
            opacity: 0;
            transform: translateY(8px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        @media (max-width: 760px) {
          :host { padding: 12px; }
          h1 { font-size: 26px; }
          .grid {
            grid-template-columns: 1fr;
            grid-template-areas:
              "options"
              "allowed"
              "regions"
              "blocked"
              "networks";
          }
          .options, .policy-choices { grid-template-columns: 1fr; }
          form { grid-template-columns: 1fr; }
          .custom-region-fields { grid-template-columns: 1fr; }
          .threshold { max-width: none; }
          .geoip-status { align-items: flex-start; flex-direction: column; }
          .toast {
            right: 12px;
            bottom: 12px;
            max-width: calc(100vw - 24px);
          }
        }
      </style>
      <div class="wrap">
        <header>
          <div class="brand">
            <img src="/api/ip_ban_manager/icon.png" alt="">
            <div class="brand-title">
              <h1>IP Ban Manager</h1>
              <span class="version" id="version">${this._initialVersionLabel()}</span>
            </div>
          </div>
        </header>
        <div id="content"><section><div class="body">Loading...</div></section></div>
        <div id="toast" class="toast" hidden></div>
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
    versionEl.textContent = this._formatVersion(version);
  }

  _initialVersionLabel() {
    return this._formatVersion(PANEL_VERSION);
  }

  _formatVersion(version) {
    if (!version || version === PANEL_VERSION_PLACEHOLDER) {
      return "";
    }
    return version.startsWith("v") ? version : `v${version}`;
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
      this._updateChromeLabels();
      this._renderToast();
      return;
    }

    const status = this._data.status || {};
    const settings = this._data.settings || {};
    const geoip = this._data.geoip || {};
    this._updateChromeLabels();
    content.innerHTML = `
      <div class="grid">
        ${this._optionsSection(settings)}
        ${this._listSection(this._t("allowed_ips.title"), this._t("allowed_ips.hint"), this._allowlistRows(settings), "remove_allowlist", "add_allowlist", this._t("allowed_ips.placeholder"), this._silencedAllowlistedLogins(settings), this._riskyAllowlistRemoveConfirm(settings), "allowed-ips-section")}
        ${this._banSection(status.banned_ips || [])}
        ${geoip.geoip_database_present ? this._allowedRegionSection(settings) : ""}
        ${this._blockedNetworksSection(settings)}
      </div>
    `;
    this._renderToast();
    this._wireEvents();
  }

  _renderToast() {
    const toast = this.shadowRoot?.getElementById("toast");
    if (!toast) {
      return;
    }
    if (!this._toast) {
      toast.hidden = true;
      toast.textContent = "";
      toast.removeAttribute("role");
      toast.removeAttribute("aria-live");
      return;
    }
    toast.className = `toast ${this._toastType === "error" ? "error" : "success"}`;
    toast.setAttribute("role", this._toastType === "error" ? "alert" : "status");
    toast.setAttribute(
      "aria-live",
      this._toastType === "error" ? "assertive" : "polite"
    );
    toast.textContent = this._toast;
    toast.hidden = false;
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

  _listSection(title, hint, rows, removeAction, addAction, placeholder, extra = "", removeConfirm = "", className = "") {
    const addLabel = addAction === "add_ban" ? this._t("block") : this._t("add");
    return `
      <section class="${this._escape(className)}">
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
      <section class="blocked-ips-section">
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

  _blockedNetworksSection(settings) {
    const rows = this._networkRows(
      settings.blocked_network_entries || settings.blocked_networks || []
    );
    return this._listSection(
      this._t("blocked_networks.title"),
      this._t("blocked_networks.hint"),
      rows,
      "remove_blocked_network",
      "add_blocked_network",
      this._t("blocked_networks.placeholder"),
      "",
      "",
      "blocked-networks-section"
    );
  }

  _allowedRegionSection(settings) {
    return `
      <section class="allowed-regions-section">
        <h2>${this._t("allowed_regions.title")}</h2>
        <div class="body">
          <p class="hint">${this._t("allowed_regions.hint")}</p>
          <div class="allowed-region-warning">${this._t("allowed_regions.warning")}</div>
          ${this._allowedRegionControls(settings)}
        </div>
      </section>
    `;
  }

  _optionsSection(settings) {
    const geoip = this._data?.geoip || {};
    const backup = this._data?.backup || {};
    return `
      <section class="options-section">
        <h2>${this._t("options")}</h2>
        <div class="body">
          ${this._healthSummary(this._data?.status?.health)}
          <div class="options">
            ${this._checkbox("auto_ban_enabled", this._t("settings.auto_ban_enabled"), this._t("settings.auto_ban_enabled_hint"), settings.auto_ban_enabled)}
            ${this._checkbox("ban_notifications_enabled", this._t("settings.ban_notifications_enabled"), this._t("settings.ban_notifications_enabled_hint"), settings.ban_notifications_enabled)}
            ${this._checkbox("allowlisted_login_notifications_enabled", this._t("settings.allowlisted_login_notifications_enabled"), this._t("settings.allowlisted_login_notifications_enabled_hint"), settings.allowlisted_login_notifications_enabled)}
            ${this._checkbox("callback_route_protection_enabled", this._t("settings.callback_route_protection_enabled"), this._t("settings.callback_route_protection_enabled_hint"), settings.callback_route_protection_enabled !== false)}
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

  _allowedRegionControls(settings) {
    const geoip = this._data?.geoip || {};
    const local = geoip.local_region || {};
    const mode = settings.allowed_region_mode || "anywhere";
    const localDetail = local.location || local.ip_address || "";
    const localCountry = local.country_code || this._countryCodeFromLocation(localDetail);
    const localSubdivision =
      local.subdivision_code || this._subdivisionCodeFromLocation(localDetail, localCountry);
    const localCountryName = local.country_name || "";
    const localSubdivisionLabel = local.subdivision_label || "";
    const country = settings.allowed_region_country || "";
    const subdivision = settings.allowed_region_subdivision || "";
    const isLocalCountry = mode === "country" && country && country === localCountry;
    const isLocalSubdivision =
      mode === "subdivision" && subdivision && subdivision === localSubdivision;
    const selectedPolicy =
      mode === "anywhere"
        ? "anywhere"
        : isLocalCountry
          ? "local_country"
          : isLocalSubdivision
            ? "local_subdivision"
            : "custom";
    const localCountryValue = this._regionDisplay(
      localCountryName || this._countryName(localCountry),
      localCountry
    );
    const localSubdivisionValue = this._subdivisionDisplay(
      localSubdivisionLabel,
      localSubdivision
    );
    return `
      <div class="policy-choices">
        ${this._regionPolicyCard("anywhere", selectedPolicy, "anywhere", "", "", this._t("allowed_regions.anywhere"), this._t("allowed_regions.anywhere_hint"))}
        ${this._regionPolicyCard("local_country", selectedPolicy, "country", localCountry, "", localCountryValue || this._t("allowed_regions.local_country"), this._t("allowed_regions.local_country_hint"), !localCountry, true)}
        ${this._regionPolicyCard("local_subdivision", selectedPolicy, "subdivision", localCountry, localSubdivision, localSubdivisionValue || this._t("allowed_regions.local_subdivision"), this._t("allowed_regions.local_subdivision_hint"), !localSubdivision, true)}
        <label class="policy-card custom restrictive">
          <input type="radio" name="allowed-region-policy" value="custom" data-region-mode="${mode === "subdivision" ? "subdivision" : "country"}" data-region-country="${this._escape(country)}" data-region-subdivision="${this._escape(subdivision)}" ${selectedPolicy === "custom" ? "checked" : ""}>
          <span>
            <strong>${this._t("allowed_regions.custom")}</strong>
            <small>${this._t("allowed_regions.custom_hint")}</small>
            <div class="custom-region-fields">
              <input id="allowed-region-country" maxlength="2" value="${this._escape(country)}" placeholder="${this._t("allowed_regions.country_placeholder")}">
              <input id="allowed-region-subdivision" maxlength="8" value="${this._escape(subdivision)}" placeholder="${this._t("allowed_regions.subdivision_placeholder")}">
            </div>
          </span>
        </label>
      </div>
      <div class="actions">
        <button class="primary" id="save-region-options" ${this._busy ? "disabled" : ""}>${this._t("apply")}</button>
      </div>
    `;
  }

  _regionPolicyCard(policy, selectedPolicy, mode, country, subdivision, label, hint, disabled = false, restrictive = false) {
    return `
      <label class="policy-card ${restrictive ? "restrictive" : ""}">
        <input
          type="radio"
          name="allowed-region-policy"
          value="${policy}"
          data-region-mode="${mode}"
          data-region-country="${this._escape(country)}"
          data-region-subdivision="${this._escape(subdivision)}"
          ${selectedPolicy === policy ? "checked" : ""}
          ${disabled ? "disabled" : ""}
        >
        <span>
          <strong>${label}</strong>
          <small>${hint}</small>
        </span>
      </label>
    `;
  }

  _shortSubdivisionCode(code) {
    return code && code.includes("-") ? code.split("-").pop() : code;
  }

  _subdivisionDisplay(name, code) {
    const shortCode = this._shortSubdivisionCode(code);
    if (name && shortCode && name !== shortCode) {
      return `${name} (${shortCode})`;
    }
    return name || shortCode || code || "";
  }

  _countryCodeFromLocation(location) {
    if (!location) {
      return "";
    }
    const parts = location.split(",").map((part) => part.trim()).filter(Boolean);
    const country = parts[parts.length - 1] || "";
    return /^[A-Z]{2}$/.test(country) ? country : "";
  }

  _subdivisionCodeFromLocation(location, country) {
    if (!location || !country) {
      return "";
    }
    const parts = location.split(",").map((part) => part.trim()).filter(Boolean);
    const subdivision = parts.length >= 2 ? parts[parts.length - 2] : "";
    if (!/^[A-Z0-9]{1,3}$/.test(subdivision)) {
      return "";
    }
    return `${country}-${subdivision}`;
  }

  _countryName(code) {
    if (!code || typeof Intl?.DisplayNames !== "function") {
      return "";
    }
    try {
      return new Intl.DisplayNames([this._language()], { type: "region" }).of(code) || "";
    } catch (_err) {
      return "";
    }
  }

  _regionDisplay(name, code) {
    if (name && code && name !== code) {
      return `${name} (${code})`;
    }
    return name || code || "";
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
    const buttonLabel = installed ? this._t("geoip.update") : this._t("geoip.download");
    return `
      <div class="geoip-status">
        <div>
          <strong>${this._t("geoip.title")}</strong>
          <small>${status}</small>
          <small>${this._t("geoip.attribution")} <a href="https://db-ip.com" target="_blank" rel="noreferrer">DB-IP City Lite</a></small>
        </div>
        <button data-action="update_geoip" ${this._busy ? "disabled" : ""}>${buttonLabel}</button>
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
          this._showToast(this._error, "error");
        }
      });
    }
    this.shadowRoot.querySelectorAll(".custom-region-fields input").forEach((input) => {
      input.addEventListener("input", () => {
        const custom = this.shadowRoot.querySelector(
          'input[name="allowed-region-policy"][value="custom"]'
        );
        if (custom) {
          custom.checked = true;
        }
      });
    });
    ["save-options", "save-region-options"].forEach((id) => {
      const button = this.shadowRoot.getElementById(id);
      if (button) {
        button.addEventListener("click", () => {
          this._post("set_options", { options: this._optionValues() });
        });
      }
    });
  }

  _optionValues() {
    const options = {};
    this.shadowRoot.querySelectorAll("input[data-option]").forEach((input) => {
      options[input.dataset.option] = input.checked;
    });
    options.login_attempts_threshold = Number(
      this.shadowRoot.getElementById("threshold").value || 0
    );
    const regionPolicy = this.shadowRoot.querySelector(
      'input[name="allowed-region-policy"]:checked'
    );
    if (regionPolicy?.value === "custom") {
      const country =
        this.shadowRoot.getElementById("allowed-region-country")?.value || "";
      const subdivision =
        this.shadowRoot.getElementById("allowed-region-subdivision")?.value || "";
      options.allowed_region_country = country;
      options.allowed_region_subdivision = subdivision;
      options.allowed_region_mode = subdivision ? "subdivision" : country ? "country" : "anywhere";
    } else if (regionPolicy) {
      options.allowed_region_mode = regionPolicy.dataset.regionMode || "anywhere";
      options.allowed_region_country = regionPolicy.dataset.regionCountry || "";
      options.allowed_region_subdivision = regionPolicy.dataset.regionSubdivision || "";
    } else {
      options.allowed_region_mode = "anywhere";
      options.allowed_region_country = "";
      options.allowed_region_subdivision = "";
    }
    return options;
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

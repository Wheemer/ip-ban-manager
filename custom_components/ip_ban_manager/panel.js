const PANEL_VERSION = "__VERSION__";
const PANEL_ELEMENT_NAME = "__PANEL_ELEMENT_NAME__".startsWith("__") ? "ip-ban-manager-panel" : "__PANEL_ELEMENT_NAME__";
const PANEL_VERSION_PLACEHOLDER = "__" + "VERSION__";
const TOAST_SUCCESS_DURATION_MS = 3500;
const TOAST_ERROR_DURATION_MS = 6500;
const TOAST_STORAGE_KEY = "ip_ban_manager.pending_toast";
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
    this._restoreToast();
    this._autoRefresh = window.setInterval(() => this._scheduleLoad(), 10000);
  }

  disconnectedCallback() {
    this._cancelConfirmation?.();
    this._updateGeneration = (this._updateGeneration || 0) + 1;
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

  async _withTimeout(promise, timeoutMs = 30000) {
    let timer;
    try {
      return await Promise.race([
      promise,
      new Promise((_, reject) => {
        timer = window.setTimeout(
          () => reject(new Error(this._requestFailedMessage())),
          timeoutMs
        );
      }),
      ]);
    } finally {
      window.clearTimeout(timer);
    }
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
    if (this._busy || this._loading || (!silent && this._isEditing())) {
      return;
    }
    const canStayQuiet = silent && this._data;
    const generation = this._updateGeneration || 0;
    let loaded = false;
    this._loading = true;
    if (!canStayQuiet) {
      this._busy = true;
      this._error = "";
      this._renderSafely();
    }
    try {
      const result = await this._withTimeout(this._api("GET", this._statusPath()));
      if (generation !== (this._updateGeneration || 0)) return;
      this._data = result;
      this._error = "";
      loaded = true;
    } catch (err) {
      if (!canStayQuiet) {
        this._error = this._errorMessage(err);
      }
    } finally {
      this._loading = false;
      if (generation === (this._updateGeneration || 0)) {
        this._busy = false;
      }
      if (generation === (this._updateGeneration || 0) && (!canStayQuiet || loaded)) {
        if (canStayQuiet) this._renderIncremental();
        else this._renderSafely();
      }
    }
  }

  _scheduleLoad() {
    if (!this._loaded || this._busy) {
      return;
    }
    window.clearTimeout(this._loadTimer);
    this._loadTimer = window.setTimeout(() => this._load({ silent: true }), 300);
  }

  async _post(action, extra = {}) {
    if (this._busy) return false;
    const previousData = this._data;
    const formDrafts = this._captureFormDrafts();
    const sourceSection = this.shadowRoot?.activeElement?.closest("section")?.classList[0];
    const optionDrafts = action === "set_options" ? this._captureOptionDrafts() : new Map();
    this._updateGeneration = (this._updateGeneration || 0) + 1;
    this._busy = true;
    this._error = "";
    this._toast = "";
    this._toastType = "success";
    if (action === "update_geoip") {
      this._showToast(this._t("geoip.downloading"), "success", false);
    }
    this._renderSafely();
    this._restoreFormDrafts(formDrafts);
    this._restoreOptionDrafts(optionDrafts);
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
      this._showToast(
        this._successMessage(action, previousData, this._data, extra),
        "success"
      );
      ok = true;
    } catch (err) {
      this._error = this._errorMessage(err);
      this._showToast(this._error, "error");
      if (action.startsWith("npm_") || Object.hasOwn(extra.options || {}, "npm_edge_protection_enabled")) {
        try {
          this._data = await this._withTimeout(this._api("GET", this._statusPath()));
        } catch (_) {
          // Keep the original action error if status is also unavailable.
        }
      }
    } finally {
      this._busy = false;
      this._renderSafely();
      this._restoreFormDrafts(formDrafts, ok ? (action === "set_options" ? null : sourceSection) : null, ok ? Object.keys(extra.options || {}) : []);
      if (ok) Object.keys(extra.options || {}).forEach(key => optionDrafts.delete(key));
      this._restoreOptionDrafts(optionDrafts);
    }
    return ok;
  }

  _enhanceCommands(root = this.shadowRoot) {
    if (!customElements.get("ha-button")) return;
    root.querySelectorAll("button").forEach(button => {
      if (button.closest("form") || button.hasAttribute("form") || button.matches("[role=tab], [data-toast-close]")) return;
      const command = document.createElement("ha-button");
      for (const attribute of button.attributes) command.setAttribute(attribute.name, attribute.value);
      command.appearance = button.classList.contains("primary") ? "accent" : button.classList.contains("secondary") ? "outlined" : button.classList.contains("danger") ? "plain" : "filled";
      command.variant = button.classList.contains("danger") ? "danger" : button.classList.contains("blocking") ? "warning" : button.classList.contains("primary") || button.classList.contains("secondary") ? "brand" : "neutral";
      command.disabled = button.disabled;
      command.append(...button.childNodes);
      button.replaceWith(command);
    });
  }

  _confirm(message) {
    if (this._confirmationPending || this._busy) return Promise.resolve(false);
    this._confirmationPending = true;
    const previousFocus = this.shadowRoot.activeElement;
    const dialog = document.createElement("dialog");
    dialog.className = "confirmation-dialog";
    dialog.setAttribute("aria-labelledby", "confirmation-title");
    dialog.setAttribute("aria-describedby", "confirmation-message");
    dialog.innerHTML = `<h2 id="confirmation-title">${this._escape(this._t("confirmation.title"))}</h2><p id="confirmation-message"></p><div class="dialog-actions"><button type="button" data-choice="cancel" autofocus>${this._escape(this._t("confirmation.cancel"))}</button><button type="button" class="primary" data-choice="confirm">${this._escape(this._t("confirmation.continue"))}</button></div>`;
    dialog.querySelector("p").textContent = message;
    this.shadowRoot.append(dialog);
    this._enhanceCommands(dialog);
    return new Promise(resolve => {
      const finish = accepted => {
        this._confirmationPending = false;
        this._cancelConfirmation = null;
        dialog.close();
        dialog.remove();
        if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
        resolve(accepted);
      };
      this._cancelConfirmation = () => finish(false);
      dialog.addEventListener("cancel", event => { event.preventDefault(); finish(false); });
      dialog.querySelector('[data-choice="cancel"]').addEventListener("click", () => finish(false));
      dialog.querySelector('[data-choice="confirm"]').addEventListener("click", () => finish(true));
      dialog.showModal();
    });
  }

  _showToast(message, type = "success", autoHide = true) {
    this._toast = message || "";
    this._toastType = type;
    this._renderToast();
    if (autoHide) {
      const duration =
        type === "error" ? TOAST_ERROR_DURATION_MS : TOAST_SUCCESS_DURATION_MS;
      this._storeToast(duration);
      this._scheduleToastClear(duration);
    } else {
      this._clearStoredToast();
      window.clearTimeout(this._toastTimer);
    }
  }

  _scheduleToastClear(duration) {
    window.clearTimeout(this._toastTimer);
    if (!this._toast) {
      return;
    }
    this._toastTimer = window.setTimeout(() => {
      this._toast = "";
      this._clearStoredToast();
      this._renderToast();
    }, duration);
  }

  _storeToast(duration) {
    try {
      window.sessionStorage?.setItem(
        TOAST_STORAGE_KEY,
        JSON.stringify({
          message: this._toast,
          type: this._toastType,
          expiresAt: Date.now() + duration,
        })
      );
    } catch (_) {
      // Storage can be unavailable in restricted browser sessions.
    }
  }

  _restoreToast() {
    if (this._toast) {
      return;
    }
    try {
      const stored = JSON.parse(
        window.sessionStorage?.getItem(TOAST_STORAGE_KEY) || "null"
      );
      const remaining = Number(stored?.expiresAt) - Date.now();
      if (!stored?.message || remaining <= 0) {
        this._clearStoredToast();
        return;
      }
      this._toast = String(stored.message);
      this._toastType = stored.type === "error" ? "error" : "success";
      this._renderToast();
      this._scheduleToastClear(remaining);
    } catch (_) {
      this._clearStoredToast();
    }
  }

  _clearStoredToast() {
    try {
      window.sessionStorage?.removeItem(TOAST_STORAGE_KEY);
    } catch (_) {
      // Storage can be unavailable in restricted browser sessions.
    }
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

  _sameValue(left, right) {
    return JSON.stringify(left ?? null) === JSON.stringify(right ?? null);
  }

  _changedOptionKeys(before, after, options = {}) {
    const npmKeys = {
      npm_edge_protection_enabled: "enabled",
      npm_protect_all_domains: "protect_all_domains",
    };
    return Object.keys(options).filter((key) => {
      if (key === "confirmed") return false;
      if (npmKeys[key]) {
        return !this._sameValue(
          before?.nginx_proxy_manager?.[npmKeys[key]],
          after?.nginx_proxy_manager?.[npmKeys[key]]
        );
      }
      return !this._sameValue(before?.settings?.[key], after?.settings?.[key]);
    });
  }

  _actionChanged(action, before, after, extra) {
    const paths = {
      add_allowlist: ["settings", "ip_addresses"],
      remove_allowlist: ["settings", "ip_addresses"],
      add_blocked_network: ["settings", "blocked_networks"],
      remove_blocked_network: ["settings", "blocked_networks"],
      add_ban: ["status", "banned_ips"],
      remove_ban: ["status", "banned_ips"],
      set_public_region: ["settings", "public_region_rules"],
      remove_public_region: ["settings", "public_region_rules"],
      silence_allowlisted_login: ["settings", "silenced_allowlisted_login_ips"],
      unsilence_allowlisted_login: ["settings", "silenced_allowlisted_login_ips"],
    };
    if (action === "set_options") {
      return this._changedOptionKeys(before, after, extra.options).length > 0;
    }
    const path = paths[action];
    if (!path || !before || !after) return true;
    const read = (source) => path.reduce((value, key) => value?.[key], source);
    return !this._sameValue(read(before), read(after));
  }

  _optionLabel(key) {
    const keys = {
      auto_ban_enabled: "settings.auto_ban_enabled",
      ban_notifications_enabled: "settings.ban_notifications_enabled",
      blocked_request_logging_enabled: "settings.blocked_request_logging_enabled",
      callback_route_protection_enabled: "settings.callback_route_protection_enabled",
      allowlisted_login_notifications_enabled: "settings.allowlisted_login_notifications_enabled",
      allowlisted_logins_can_ban: "settings.allowlisted_logins_can_ban",
      default_deny_enabled: "settings.default_deny_enabled",
      geoip_enabled: "settings.geoip_enabled",
      sidebar_panel_enabled: "settings.sidebar_panel_enabled",
      login_attempts_threshold: "settings.login_attempts_threshold",
      public_region_enabled: "region_list.enabled",
      npm_edge_protection_enabled: "npm.edge_protection",
      npm_protect_all_domains: "npm.protect_all_domains",
    };
    return this._t(keys[key] || key);
  }

  _localizedList(values) {
    try {
      return new Intl.ListFormat(this._language(), {
        style: "short",
        type: "conjunction",
      }).format(values);
    } catch (_) {
      return values.join(", ");
    }
  }

  _successMessage(action, before = null, after = this._data, extra = {}) {
    const path = this._data?.backup?.path || "/config/ip_ban_manager/ip-ban-manager-backup.yaml";
    const fallbacks = {
      set_options: "Options applied.",
      update_geoip: "GeoIP database updated.",
      export_config: `Saved backup to ${path}`,
      import_config: `Restored backup from ${path}`,
      download_config: "Backup downloaded.",
      upload_config: "Backup uploaded and applied.",
      clear_activity_history: "Activity history cleared.",
      npm_connect: "Connected to Nginx Proxy Manager.",
      npm_select_host: "Proxy host selected.",
      npm_sync: "Nginx Proxy Manager rules synchronized.",
      npm_disconnect: "Nginx Proxy Manager disconnected.",
    };
    if (!this._actionChanged(action, before, after, extra)) {
      return this._t("success.no_changes");
    }
    const key = `success.${action}`;
    const translated = this._data?.translations?.[key]
      ? this._t(key, { path })
      : fallbacks[action] || "Done.";
    if (action === "set_options") {
      const labels = this._changedOptionKeys(before, after, extra.options)
        .map((option) => this._optionLabel(option));
      return labels.length
        ? `${translated} ${this._localizedList(labels)}`
        : translated;
    }
    const valueActions = {
      add_allowlist: ["add", "allowed_ips.title"],
      remove_allowlist: ["remove", "allowed_ips.title"],
      add_ban: ["block", "blocked_ips.title"],
      remove_ban: ["remove", "blocked_ips.title"],
      add_blocked_network: ["add", "blocked_networks.title"],
      remove_blocked_network: ["remove", "blocked_networks.title"],
      set_public_region: ["apply", "allowed_regions.title"],
      remove_public_region: ["remove", "allowed_regions.title"],
      silence_allowlisted_login: ["add", "silenced_logins.title"],
      unsilence_allowlisted_login: ["remove", "silenced_logins.title"],
    };
    if (valueActions[action] && extra.value) {
      const [verb, section] = valueActions[action];
      const threshold = action === "set_public_region"
        ? ` · ${Number(extra.threshold)} ${this._t("regional_thresholds.attempts")}`
        : "";
      return `${this._t(verb)} · ${this._t(section)} · ${extra.value}${threshold}`;
    }
    if (this._data?.translations?.[key]) {
      return this._t(key, { path });
    }
    return translated;
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
        .wrap { max-width: 1180px; margin: 0 auto; min-height: calc(100vh - 48px); display: flex; flex-direction: column; }
        #content { flex: 1; }
        footer {
          display: flex;
          align-items: center;
          justify-content: flex-end;
          gap: 16px;
          padding-top: 24px;
          color: var(--secondary-text-color);
        }
        .brand { display: flex; align-items: center; gap: 8px; min-width: 0; }
        .brand img { width: 20px; height: 20px; object-fit: contain; }
        .brand-title {
          display: flex;
          align-items: baseline;
          gap: 10px;
          flex-wrap: wrap;
          min-width: 0;
        }
        footer h1 { margin: 0; font-size: 12px; line-height: 1.4; font-weight: 500; }
        .version {
          color: var(--secondary-text-color);
          font-size: 12px;
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
        .panel-tabs { position: sticky; top: 0; z-index: 5; grid-column: 1 / -1; display: flex; gap: 4px; width: 100%; min-width: 0; overflow-x: auto; background: var(--primary-background-color); border-bottom: 1px solid var(--divider-color); }
        .panel-tabs button { flex: 0 0 auto; min-height: 44px; padding: 10px 16px; background: transparent; color: var(--secondary-text-color); border: 0; border-bottom: 3px solid transparent; border-radius: 0; cursor: pointer; }
        .panel-tabs button[aria-selected="true"] { color: var(--primary-color); border-bottom-color: var(--primary-color); }
        .panel-tabs button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: -2px; }
        .tab-page { grid-column: 1 / -1; min-width: 0; }
        .tab-page[hidden] { display: none; }
        .npm-section .npm-block { margin: 0; }
        .npm-section .npm-settings { padding: 0; border: 0; background: transparent; }
        .npm-section .npm-settings h3 { display: none; }
        .geoip-section .geoip-status { margin: 0; padding: 0; border: 0; background: transparent; flex-wrap: wrap; }
        .geoip-section .geoip-status strong { display: none; }
        .tab-page input, .tab-page select { min-width: 0; }
        .hint { line-height: 1.45; }
        .tab-page button { min-height: 38px; }
        .grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 16px;
          align-items: start;
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
          padding: 14px 16px;
          font-size: 17px;
          line-height: 1.3;
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
        .activity-row {
          display: flex;
          align-items: flex-start;
          justify-content: space-between;
          gap: 16px;
          min-height: 48px;
          padding: 9px 10px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--secondary-background-color);
        }
        .activity-row + .activity-row { margin-top: 8px; }
        .activity-row strong { display: block; overflow-wrap: anywhere; }
        .activity-row small,
        .activity-row time { color: var(--secondary-text-color); font-size: 13px; }
        .activity-row time { flex: 0 0 auto; white-space: nowrap; }
        .activity-row-actions { display: grid; justify-items: end; gap: 8px; flex: 0 0 auto; }
        .activity-location { margin-top: 2px; }
        .activity-detail { margin-top: 4px; color: var(--secondary-text-color); font-size: 13px; overflow-wrap: anywhere; }
        .activity-tabs { display: flex; gap: 4px; margin-bottom: 16px; border-bottom: 1px solid var(--divider-color); }
        .activity-tabs button { min-height: 44px; padding: 10px 16px; border: 0; border-bottom: 3px solid transparent; border-radius: 0; background: transparent; color: var(--secondary-text-color); cursor: pointer; }
        .activity-tabs button.active { border-bottom-color: var(--primary-color); color: var(--primary-color); }
        .activity-actions { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
        .activity-retention { margin: 12px 0 0; color: var(--secondary-text-color); font-size: 13px; }
        .empty { color: var(--secondary-text-color); }
        .region-toggle-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          min-height: 42px;
          margin-bottom: 12px;
          padding: 8px 10px;
          border: 1px solid var(--warning-color, #ffa600);
          border-radius: 6px;
          background: color-mix(in srgb, var(--warning-color, #ffa600) 10%, var(--secondary-background-color));
        }
        .region-toggle-row label {
          display: flex;
          align-items: center;
          gap: 8px;
          font-weight: 700;
        }
        .region-toggle-row input { width: 18px; min-width: 18px; flex: 0 0 18px; margin: 0; }
        .region-shortcuts { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 12px; }
        .region-shortcuts button { display: inline-flex; align-items: center; gap: 8px; min-height: 40px; padding: 8px 12px; border: 1px solid var(--primary-color); border-radius: 6px; background: var(--secondary-background-color); color: var(--primary-text-color); font-weight: 400; white-space: normal; text-align: left; cursor: pointer; }
        .region-shortcuts button:hover:not(:disabled) { background: var(--divider-color); }
        .region-shortcuts button:focus-visible { outline: 2px solid var(--primary-color); outline-offset: 2px; }
        .region-shortcuts ha-icon { --mdc-icon-size: 20px; flex: 0 0 20px; }
        .region-rows { display: grid; gap: 8px; margin-bottom: 14px; }
        .region-entry {
          grid-template-columns: minmax(0, 1fr) 90px auto auto;
          align-items: end;
          padding: 10px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--secondary-background-color);
        }
        .region-entry label,
        #regional-threshold-form label {
          display: grid;
          gap: 5px;
          min-width: 0;
          color: var(--secondary-text-color);
          font-size: 12px;
        }
        .region-entry input { width: 100%; box-sizing: border-box; }
        .region-apply-unchanged { visibility: hidden; pointer-events: none; }
        #regional-threshold-form { grid-template-columns: minmax(0, 1fr) 90px auto; align-items: end; }
        #regional-threshold-form input { width: 100%; box-sizing: border-box; }
        @media (max-width: 480px) {
          #regional-threshold-form { grid-template-columns: minmax(0, 1fr) 90px; }
          #regional-threshold-form button { grid-column: 1 / -1; justify-self: end; }
          .region-entry { grid-template-columns: minmax(0, 1fr) 90px; }
        }
        .meta { color: var(--secondary-text-color); font-size: 13px; margin-top: 2px; }
        .empty {
          color: var(--secondary-text-color);
          padding: 12px;
          border: 1px dashed var(--divider-color);
          border-radius: 6px;
          margin-bottom: 14px;
        }
        form { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 8px; }
        select {
          width: 100%;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--secondary-background-color);
          color: inherit;
          padding: 10px 12px;
          font: inherit;
        }
        .npm-details small {
          color: var(--secondary-text-color);
          font-size: 13px;
          line-height: 1.35;
        }
        .npm-connect-form {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 8px;
        }
        .npm-connect-form #npm-url { grid-column: 1 / -1; }
        .npm-details { display: grid; gap: 6px; }
        .npm-controls { display: grid; gap: 12px; margin-top: 12px; }
        .npm-block { margin-top: 14px; }
        .npm-settings {
          padding: 10px 12px;
          border: 1px solid var(--divider-color);
          border-radius: 6px;
          background: var(--secondary-background-color);
        }
        .npm-settings h3 {
          margin: 0 0 6px;
          font-size: 15px;
        }
        .npm-configured-layout {
          display: grid;
          grid-template-columns: minmax(0, 1fr) minmax(260px, .85fr);
          align-items: start;
          gap: 16px;
        }
        .npm-configured-layout .npm-controls { margin-top: 0; }
        .npm-action-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          margin-top: 12px;
        }
        .npm-action-row-connect { justify-content: flex-end; }
        .npm-action-row-apply { justify-content: flex-end; }
        .npm-disconnect-row {
          display: flex;
          justify-content: flex-start;
          margin-top: 10px;
        }
        .npm-action-row .button-row { justify-content: flex-end; }
        .npm-select-row {
          display: grid;
          grid-template-columns: minmax(0, 1fr) auto;
          gap: 8px;
        }
        .options { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 12px; }
        .advanced-title {
          margin: 12px 0 6px;
          color: var(--secondary-text-color);
          font-weight: 600;
        }
        label.check { display: flex; align-items: flex-start; gap: 10px; }
        label.check input { width: 16px; height: 16px; flex: 0 0 16px; margin: 2px 0 0; }
        label.check span { display: grid; gap: 2px; }
        label.check small {
          color: var(--secondary-text-color);
          font-size: 12px;
          line-height: 1.25;
        }
        label.check.risky {
          padding: 7px;
          border: 1px solid var(--warning-color, #ffa600);
          border-radius: 6px;
          background: rgba(255, 152, 0, 0.10);
        }
        .threshold {
          margin-top: 10px;
          max-width: 310px;
        }
        .threshold label {
          display: grid;
          grid-template-columns: minmax(0, 1fr) 88px;
          align-items: center;
          gap: 12px;
        }
        .threshold .hint { margin: 0; }
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
          grid-template-columns: repeat(auto-fit, minmax(min(100%, 390px), 1fr));
          gap: 10px;
          margin-top: 10px;
        }
        .backup-stack .geoip-status {
          margin-top: 0;
          min-width: 0;
        }
        .backup-stack .geoip-status > div:first-child { min-width: 0; }
        .backup-stack .geoip-status small { overflow-wrap: anywhere; }
        .backup-stack .geoip-status > .button-row {
          display: flex;
          flex: 0 0 auto;
          flex-direction: column;
          flex-wrap: nowrap;
          align-items: stretch;
        }
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
          margin-top: 12px;
          margin-bottom: 12px;
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
          .wrap { min-height: calc(100vh - 24px); }
          .grid {
            display: flex;
            flex-direction: column;
          }
          .tab-page { width: 100%; }
          .tab-page section { width: 100%; }
          .panel-tabs button { flex: 1 0 auto; padding: 10px; }
          .options { grid-template-columns: 1fr; }
          form { grid-template-columns: 1fr; }
          .npm-connect-form, .npm-select-row { grid-template-columns: 1fr; }
          .npm-configured-layout { grid-template-columns: 1fr; }
          .backup-stack { grid-template-columns: 1fr; }
          .threshold { max-width: none; }
          .geoip-status { align-items: flex-start; flex-direction: column; }
          .backup-stack .geoip-status > .button-row { width: 100%; }
          .toast {
            right: 12px;
            bottom: 12px;
            max-width: calc(100vw - 24px);
          }
        }
        .tab-page > section { border: 0; border-radius: 8px; background: var(--card-background-color); overflow: hidden; }
        .tab-page > section > h2 { padding: 18px 20px 0; border: 0; font-size: 19px; font-weight: 600; }
        .tab-page > section > .body { padding: 16px 20px 20px; }
        .tab-page > section + section { margin-top: 20px; }
        .blocking-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; align-items: start; }
        .blocking-grid > section + section { margin-top: 0; }
        .blocking-grid .allowed-regions-section { grid-column: 1 / -1; }
        #page-allowed .rows { grid-template-columns: repeat(2, minmax(0, 1fr)); }
        .row { padding: 10px 12px; }
        .options { gap: 0 32px; }
        .options > label.check { padding: 12px 0; border-bottom: 1px solid var(--divider-color); }
        .options > label.check.risky { padding: 12px; margin-top: 8px; border: 1px solid var(--warning-color, #ffa600); border-radius: 6px; }
        label.check span { line-height: 1.4; min-width: 0; }
        label.check small { font-size: 13px; line-height: 1.4; }
        .subsection { padding: 16px 0; border: 0; border-top: 1px solid var(--divider-color); border-radius: 0; background: transparent; }
        .subsection h3 { font-size: 15px; margin: 0 0 12px; }
        .threshold { max-width: 380px; margin-top: 20px; }
        .advanced-title { margin-top: 24px; }
        .actions { padding-top: 16px; margin-top: 16px; border-top: 1px solid var(--divider-color); }
        #page-settings .npm-configured-layout { gap: 32px; }
        .backup-stack { display: flex; flex-direction: column; gap: 0; margin: 0; }
        .backup-stack .geoip-status { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; background: transparent; border: 0; border-radius: 0; padding: 4px 0 20px; align-items: center; }
        .backup-stack .geoip-status + .geoip-status { padding: 20px 0 4px; border-top: 1px solid var(--divider-color); }
        .backup-stack .geoip-status > .button-row { display: flex; flex-direction: row; flex-wrap: wrap; align-items: center; justify-content: flex-end; gap: 8px; }
        .backup-stack .button-row button { min-width: 90px; }
        @media (max-width: 760px) {
          .panel-tabs { gap: 0; }
          .panel-tabs button { flex: 0 0 auto; padding: 12px; }
          .blocking-grid { grid-template-columns: 1fr; gap: 16px; }
          #page-allowed .rows { grid-template-columns: 1fr; }
          .tab-page > section > h2 { padding: 16px 16px 0; font-size: 18px; }
          .tab-page > section > .body { padding: 14px 16px 16px; }
          #page-settings .npm-configured-layout { gap: 16px; }
          .backup-stack .geoip-status { grid-template-columns: 1fr; gap: 14px; }
        }
        :host { font-family: var(--ha-font-family-body, inherit); font-size: 14px; line-height: 1.5; }
        button, ha-button { min-height: 44px; }
        ha-button { --ha-button-height: 44px; }
        button:not([role="tab"]):not([data-toast-close]), ha-button::part(base) {
          box-sizing: border-box; min-height: 44px; padding: 0 20px;
          border: 1px solid var(--divider-color); border-radius: var(--ha-button-border-radius, 22px);
          background: var(--secondary-background-color); color: var(--primary-text-color);
          font: inherit; font-weight: 500; line-height: 1.25;
          transition: background-color 120ms ease, color 120ms ease, border-color 120ms ease;
        }
        button.primary:not([role="tab"]):not([data-toast-close]), ha-button.primary::part(base) { background: var(--primary-color); border-color: var(--primary-color); color: var(--text-primary-color, #fff); }
        button.danger:not([role="tab"]):not([data-toast-close]), ha-button.danger::part(base) { background: transparent; border-color: var(--divider-color); color: var(--error-color, #db4437); }
        button.secondary:not([role="tab"]):not([data-toast-close]), ha-button.secondary::part(base) { background: transparent; border-color: var(--primary-color); color: var(--primary-color); }
        @media (hover: hover) {
          button[role="tab"]:not(:disabled):hover { background: color-mix(in srgb, var(--primary-color) 12%, transparent); color: var(--primary-color); }
          button[data-toast-close]:not(:disabled):hover { background: rgba(255,255,255,.2); }
          input:not(:disabled):not([readonly]):not([type="checkbox"]):hover, select:not(:disabled):hover { border-color: var(--primary-color); background: color-mix(in srgb, var(--primary-color) 6%, var(--secondary-background-color)); }
          label:has(input[type="checkbox"]:not(:disabled)):hover input[type="checkbox"] { outline: 3px solid color-mix(in srgb, var(--primary-color) 35%, transparent); outline-offset: 3px; }
          a[href]:hover { color: var(--primary-color); text-decoration: underline; text-decoration-thickness: 2px; text-underline-offset: 3px; background: color-mix(in srgb, var(--primary-color) 12%, transparent); }
          button:not([role="tab"]):not([data-toast-close]):not(.primary):not(.secondary):not(.danger):not(.blocking):not(:disabled):hover,
          ha-button:not(.primary):not(.secondary):not(.danger):not(.blocking):not([disabled]):hover::part(base) { background: var(--divider-color); }
          button.secondary:not([role="tab"]):not([data-toast-close]):not(:disabled):hover, ha-button.secondary:not([disabled]):hover::part(base) { background: color-mix(in srgb, var(--primary-color) 18%, var(--secondary-background-color)); }
          button.blocking:not([role="tab"]):not([data-toast-close]):not(:disabled):hover, ha-button.blocking:not([disabled]):hover::part(base) { background: color-mix(in srgb, var(--warning-color, #ffa600) 20%, var(--secondary-background-color)); border-color: var(--warning-color, #ffa600); }
          button.primary:not([role="tab"]):not([data-toast-close]):not(:disabled):hover, ha-button.primary:not([disabled]):hover::part(base) { background: color-mix(in srgb, var(--primary-color) 70%, #000); border-color: color-mix(in srgb, var(--primary-color) 70%, #000); }
          button.danger:not([role="tab"]):not([data-toast-close]):not(:disabled):hover, ha-button.danger:not([disabled]):hover::part(base) { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); color: #fff; }
        }
        button.danger:not([role="tab"]):not([data-toast-close]):not(:disabled):active, ha-button.danger:not([disabled]):active::part(base) { background: var(--error-color, #db4437); border-color: var(--error-color, #db4437); color: #fff; }
        ha-button[disabled]::part(base) { opacity: .55; cursor: not-allowed; }
        button.primary:disabled:not([role="tab"]):not([data-toast-close]), ha-button.primary[disabled]::part(base) { background: var(--secondary-background-color); border-color: var(--divider-color); color: var(--secondary-text-color); }
        button.secondary:disabled:not([role="tab"]):not([data-toast-close]), ha-button.secondary[disabled]::part(base) { background: var(--secondary-background-color); border-color: var(--divider-color); color: var(--secondary-text-color); }
        button:disabled { cursor: not-allowed; }
        button:focus-visible, input:focus-visible, select:focus-visible, a[href]:focus-visible, ha-button:focus-within::part(base) { outline: 2px solid var(--primary-color); outline-offset: 3px; }
        select:not(:disabled), a[href], label:has(input[type="checkbox"]:not(:disabled)) { cursor: pointer; }
        input:not([type="checkbox"]):not(:disabled) { cursor: text; }
        input:disabled, select:disabled, label:has(input[type="checkbox"]:disabled) { cursor: not-allowed; }
        input:not([type="checkbox"]), select { min-height: 44px; font-size: 14px; line-height: 1.5; border-radius: 6px; }
        input:not([type="checkbox"]):focus, select:focus { border-color: var(--primary-color); }
        .field-label { display: grid; gap: 6px; min-width: 0; color: var(--secondary-text-color); font-size: 13px; }
        .field-label input { color: var(--primary-text-color); }
        form { align-items: end; }
        .npm-url-field { grid-column: 1 / -1; }
        .options { column-gap: 24px; }
        label.check { flex-direction: row-reverse; align-items: center; gap: 16px; cursor: pointer; }
        label.check span { flex: 1; }
        label.check input[type="checkbox"] { appearance: none; -webkit-appearance: none; position: relative; width: 40px; height: 24px; flex: 0 0 40px; padding: 0; margin: 0; border: 0; border-radius: 12px; background: color-mix(in srgb, var(--primary-text-color) 38%, var(--card-background-color)); cursor: pointer; }
        label.check input[type="checkbox"]::before { content: ""; position: absolute; width: 18px; height: 18px; left: 3px; top: 3px; border-radius: 50%; background: var(--card-background-color); transition: transform 120ms ease; }
        label.check input[type="checkbox"]:checked { background: var(--switch-checked-track-color, var(--primary-color)); }
        label.check input[type="checkbox"]:checked::before { transform: translateX(16px); background: #fff; }
        label.check input[type="checkbox"]:disabled { opacity: .5; cursor: not-allowed; }
        .options > label.check { min-height: 72px; }
        .options > label.check.risky { min-height: 80px; }
        .row, .activity-row { min-height: 64px; }
        .row ha-button, .activity-row ha-button { flex-shrink: 0; }
        .empty { padding: 20px 16px; border-style: solid; color: var(--secondary-text-color); }
        .confirmation-dialog { width: 520px; max-width: calc(100vw - 32px); max-height: calc(100dvh - 48px); padding: 24px; border: 0; border-radius: 8px; color: var(--primary-text-color); background: var(--card-background-color); box-shadow: 0 16px 48px rgba(0,0,0,.32); }
        .confirmation-dialog::backdrop { background: rgba(0,0,0,.5); }
        .confirmation-dialog h2 { padding: 0; margin: 0 0 16px; border: 0; font-size: 20px; }
        .confirmation-dialog p { white-space: pre-line; line-height: 1.5; margin: 0 0 24px; overflow-wrap: anywhere; }
        .dialog-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; }
        .toast-message { flex: 1; min-width: 0; overflow-wrap: anywhere; }
        [data-toast-close] { display: grid; place-items: center; flex: 0 0 36px; width: 36px; min-height: 36px; padding: 4px; border: 0; background: transparent; }
        [data-toast-close] ha-icon { --mdc-icon-size: 20px; }
        @media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: 0ms !important; transition-duration: 0ms !important; } }
        @media (max-width: 760px) { .options > label.check { min-height: 76px; } .row { gap: 8px; } .confirmation-dialog { padding: 20px; } input:not([type="checkbox"]), select { font-size: 16px; } }
      </style>
      <div class="wrap">
        <div id="content"><section><div class="body">Loading...</div></section></div>
        <footer>
          <div class="brand">
            <img src="/api/ip_ban_manager/icon.png" alt="">
            <div class="brand-title">
              <h1>IP Ban Manager</h1>
              <span class="version" id="version">${this._initialVersionLabel()}</span>
            </div>
          </div>
        </footer>
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

    this._updateChromeLabels();
    this._sectionMarkup = this._sections();
    content.innerHTML = this._gridMarkup(this._sectionMarkup);
    this._selectPanelTab(this._panelTab || "activity");
    this._renderToast();
    this._enhanceCommands();
    this._wireEvents();
    content.setAttribute("aria-busy", String(Boolean(this._busy)));
    if (this._busy) content.querySelectorAll("input, select, ha-button, button:not([data-panel-tab])").forEach(control => { control.disabled = true; });
  }

  _gridMarkup(sections) {
    return `
      <div class="grid">
        <nav class="panel-tabs wide" role="tablist" aria-label="${this._t("title")}">
          ${["activity", "allowed", "blocking", "notifications", "settings", "backup"].map(id => `<button type="button" role="tab" id="tab-${id}" data-panel-tab="${id}" aria-controls="page-${id}" aria-selected="${(this._panelTab || "activity") === id}" tabindex="${(this._panelTab || "activity") === id ? 0 : -1}">${this._escape(this._t(`tabs.${id}`))}</button>`).join("")}
        </nav>
        <div class="tab-page" id="page-activity" role="tabpanel" aria-labelledby="tab-activity">${sections.get("activity-section") || ""}</div>
        <div class="tab-page" id="page-allowed" role="tabpanel" aria-labelledby="tab-allowed">
          ${sections.get("allowed-ips-section") || ""}${sections.get("silenced-section") || ""}
        </div>
        <div class="tab-page blocking-grid" id="page-blocking" role="tabpanel" aria-labelledby="tab-blocking">
          ${sections.get("blocked-ips-section") || ""}${sections.get("blocked-networks-section") || ""}${sections.get("allowed-regions-section") || ""}
        </div>
        <div class="tab-page" id="page-notifications" role="tabpanel" aria-labelledby="tab-notifications">${sections.get("notifications-section") || ""}</div>
        <div class="tab-page" id="page-settings" role="tabpanel" aria-labelledby="tab-settings">
          ${sections.get("options-section") || ""}${sections.get("npm-section") || ""}${sections.get("geoip-section") || ""}
        </div>
        <div class="tab-page" id="page-backup" role="tabpanel" aria-labelledby="tab-backup">${sections.get("backup-section") || ""}</div>
      </div>
    `;
  }

  _selectPanelTab(id) {
    if (!["activity", "allowed", "blocking", "notifications", "settings", "backup"].includes(id)) return;
    this._panelTab = id;
    this.shadowRoot.querySelectorAll("button[data-panel-tab]").forEach(button => {
      const selected = button.dataset.panelTab === id;
      button.setAttribute("aria-selected", String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    this.shadowRoot.querySelectorAll(".tab-page").forEach(page => {
      page.hidden = page.id !== `page-${id}`;
    });
  }

  _sectionColumn(key) {
    if (key === "activity-section") return "#page-activity";
    if (["options-section", "npm-section", "geoip-section"].includes(key)) return "#page-settings";
    if (["allowed-ips-section", "silenced-section"].includes(key)) return "#page-allowed";
    if (key === "notifications-section") return "#page-notifications";
    if (key === "backup-section") return "#page-backup";
    return "#page-blocking";
  }

  _sections() {
    const status = this._data.status || {};
    const settings = this._data.settings || {};
    const geoip = this._data.geoip || {};
    return new Map([
      ["activity-section", this._activitySection(this._data.activity || [], this._data.activity_history || [], this._data.nginx_proxy_manager || {})],
      ["options-section", this._optionsSection(settings)],
      ["notifications-section", this._notificationSection(settings)],
      ["silenced-section", `<section class="silenced-section"><h2>${this._t("silenced_logins.title")}</h2><div class="body"><p class="hint">${this._t("silenced_logins.hint")}</p>${this._rows(settings.silenced_allowlisted_login_ips || [], "unsilence_allowlisted_login")}</div></section>`],
      ["npm-section", `<section class="npm-section"><h2>${this._t("npm.title")}</h2><div class="body">${this._npmOptions(this._data.nginx_proxy_manager || {})}</div></section>`],
      ["geoip-section", `<section class="geoip-section"><h2>${this._t("geoip.title")}</h2><div class="body">${this._geoipStatus(geoip)}</div></section>`],
      ["backup-section", `<section class="backup-section"><h2>${this._t("tabs.backup")}</h2><div class="body">${this._backupStatus(this._data.backup || {})}</div></section>`],
      ["allowed-ips-section", this._listSection(this._t("allowed_ips.title"), this._t("allowed_ips.hint"), this._allowlistRows(settings), "remove_allowlist", "add_allowlist", this._t("allowed_ips.placeholder"), "", this._riskyAllowlistRemoveConfirm(settings), "allowed-ips-section")],
      ["blocked-ips-section", this._banSection(status.banned_ips || [])],
      ["allowed-regions-section", geoip.geoip_database_present || settings.public_region_enabled || Object.keys(settings.public_region_rules || {}).length ? this._allowedRegionSection(settings) : ""],
      ["blocked-networks-section", this._blockedNetworksSection(settings)],
    ]);
  }

  _activitySection(recentEvents, historyEvents, npm) {
    const history = this._activityView === "history";
    const events = history ? historyEvents : recentEvents;
    const rows = (events || []).map((event) => {
      const request = [event.method, event.path].filter(Boolean).join(" ");
      const requestCount = Number(event.request_count) || 1;
      const details = [
        this._t("activity.request_count", { count: requestCount }),
        request,
        event.status ? `HTTP ${event.status}` : "",
        event.host || "",
        event.detail || "",
      ].filter(Boolean).join(" · ");
      return `
        <div class="activity-row">
          <div>
            <strong>${this._escape(event.ip || "")}</strong>
            ${event.location ? `<small class="activity-location">${this._escape(event.location)}</small>` : ""}
            ${details ? `<div class="activity-detail">${this._escape(details)}</div>` : ""}
          </div>
          <div class="activity-row-actions">
            <time>${this._escape(this._formatDate(event.timestamp))}</time>
            <button class="blocking" data-action="add_ban" data-value="${this._escape(event.ip || "")}" aria-label="${this._escape(`${this._t("block")} ${event.ip || ""}`)}" ${this._busy ? "disabled" : ""}>${this._t("block")}</button>
          </div>
        </div>
      `;
    }).join("");
    return `
      <section class="activity-section wide">
        <h2>${this._t("activity.title")}</h2>
        <div class="body">
          <p class="hint">${this._t("activity.hint")}</p>
          ${npm.activity_error ? `<div class="health warn">${this._escape(this._t("activity.npm_error", { error: npm.activity_error }))}</div>` : ""}
          <div class="activity-tabs" role="tablist">
            <button type="button" id="activity-tab-recent" class="${history ? "" : "active"}" data-activity-view="recent" role="tab" aria-controls="activity-results" aria-selected="${!history}" tabindex="${history ? -1 : 0}">${this._t("activity.recent")}</button>
            <button type="button" id="activity-tab-history" class="${history ? "active" : ""}" data-activity-view="history" role="tab" aria-controls="activity-results" aria-selected="${history}" tabindex="${history ? 0 : -1}">${this._t("activity.history")}</button>
          </div>
          <div id="activity-results" role="tabpanel" aria-labelledby="activity-tab-${history ? "history" : "recent"}">${rows || `<div class="empty">${this._t("activity.none")}</div>`}</div>
          ${history ? `
            <div class="activity-actions">
              <p class="activity-retention">${this._t("activity.retention", { hours: this._data?.settings?.activity_history_hours ?? 24 })}</p>
              <button type="button" class="danger" data-action="clear_activity_history" data-confirm="${this._escape(this._t("activity.clear_confirm"))}">${this._t("activity.clear")}</button>
            </div>
          ` : ""}
        </div>
      </section>
    `;
  }

  _showActivityView(view, focus = false) {
    if (this._activityView === view) return;
    this._activityView = view;
    const previous = this.shadowRoot.querySelector(".activity-section");
    if (!previous) return;
    const html = this._activitySection(this._data.activity || [], this._data.activity_history || [], this._data.nginx_proxy_manager || {});
    const template = document.createElement("template");
    template.innerHTML = html;
    const next = template.content.firstElementChild;
    previous.replaceWith(next);
    this._sectionMarkup?.set("activity-section", html);
    this._enhanceCommands(next);
    this._wireEvents({ getElementById: id => next.querySelector(`#${id}`), querySelectorAll: selector => next.querySelectorAll(selector) });
    if (focus) next.querySelector(`[data-activity-view="${view}"]`).focus({ preventScroll: true });
  }

  _sectionHasDraft(section) {
    return [...section.querySelectorAll("input, select")].some((input) => {
      if (input.tagName === "SELECT") return [...input.options].some(option => option.selected !== option.defaultSelected);
      return ["checkbox", "radio"].includes(input.type) ? input.checked !== input.defaultChecked : input.value !== input.defaultValue;
    });
  }

  _renderIncremental() {
    const grid = this.shadowRoot?.querySelector("#content > .grid");
    if (!grid || !this._sectionMarkup) return;
    try {
      for (const [key, html] of this._sections()) {
        if (html === this._sectionMarkup.get(key)) continue;
        const previous = grid.querySelector(`.${key}`);
        // Preserve a focused action and unsaved inputs; other sections still update.
        if (previous && (previous.contains(this.shadowRoot.activeElement) || this._sectionHasDraft(previous))) continue;
        if (!html) previous?.remove();
        else {
          const template = document.createElement("template");
          template.innerHTML = html;
          const next = template.content.firstElementChild;
          if (previous) previous.replaceWith(next);
          else grid.querySelector(this._sectionColumn(key))?.append(next);
          this._enhanceCommands(next);
          this._wireEvents({
            getElementById: id => next.querySelector(`#${id}`),
            querySelectorAll: selector => next.querySelectorAll(selector),
          });
        }
        this._sectionMarkup.set(key, html);
      }
      this._updateChromeLabels();
    } catch (err) {
      console.error("IP Ban Manager background update failed", err);
    }
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
    toast.innerHTML = `<span class="toast-message"></span><button type="button" data-toast-close aria-label="${this._escape(this._t("confirmation.dismiss"))}"><ha-icon icon="mdi:close" aria-hidden="true"></ha-icon></button>`;
    toast.querySelector(".toast-message").textContent = this._toast;
    toast.querySelector("[data-toast-close]").addEventListener("click", () => {
      this._toast = "";
      this._clearStoredToast();
      window.clearTimeout(this._toastTimer);
      this._renderToast();
    });
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
    const addClass = ["add_ban", "add_blocked_network"].includes(addAction) ? "blocking" : "primary";
    return `
      <section class="${this._escape(className)}">
        <h2>${title}</h2>
        <div class="body">
          <p class="hint">${hint}</p>
          ${this._rows(rows, removeAction, removeConfirm)}
          <form data-action="${addAction}">
            <label class="field-label">${placeholder}<input name="value" placeholder="${placeholder}" autocomplete="off" required></label>
            <button class="${addClass}" ${this._busy || addAction === "add_allowlist" ? "disabled" : ""}>${addLabel}</button>
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
            <label class="field-label">${this._t("blocked_ips.placeholder")}<input name="value" autocomplete="off" required></label>
            <button class="blocking" ${this._busy ? "disabled" : ""}>${this._t("block")}</button>
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

  _npmOptions(npm) {
    if (!npm.configured || npm.reauth_required) {
      const suggestedUrl = npm.base_url || npm.detected_url || npm.suggested_url || "";
      return `
        <div class="npm-block">
          <div class="npm-settings">
            <h3>${this._t("npm.title")}</h3>
            <p class="hint">${this._t("npm.hint")}</p>
            ${npm.addon_detected ? `<p class="hint">${this._escape(this._t("npm.addon_detected", { name: npm.addon_name || "Nginx Proxy Manager" }))}</p>` : ""}
            <form id="npm-connect-form" class="npm-connect-form">
              <label class="field-label npm-url-field">${this._t("npm.url")}<input id="npm-url" type="url" value="${this._escape(suggestedUrl)}" autocomplete="url" required></label>
              <label class="field-label">${this._t("npm.identity")}<input id="npm-identity" type="email" value="${this._escape(npm.identity || "")}" autocomplete="username" required></label>
              <label class="field-label">${this._t("npm.secret")}<input id="npm-secret" type="password" autocomplete="current-password" required></label>
            </form>
          </div>
          <div class="npm-action-row npm-action-row-connect">
            <button class="primary" type="submit" form="npm-connect-form" ${this._busy ? "disabled" : ""}>${this._t("npm.connect")}</button>
          </div>
        </div>
      `;
    }

    const hosts = npm.hosts || [];
    const selected = hosts.find((host) => Number(host.id) === Number(npm.proxy_host_id));
    const selectedLabel = selected ? (selected.domain_names || []).join(", ") : "";
    const lastSync = npm.last_sync ? this._formatDate(npm.last_sync) : "";
    const connectionDetails = [
      npm.base_url || "",
      lastSync ? this._t("npm.last_sync", { date: lastSync }) : "",
    ].filter(Boolean).join(" · ");
    const hostOptions = hosts.map((host) => {
      const label = (host.domain_names || []).join(", ");
      return `<option value="${Number(host.id)}" ${Number(host.id) === Number(npm.proxy_host_id) ? "selected" : ""}>${this._escape(label)}</option>`;
    }).join("");
    return `
      <div class="npm-block">
        <div class="npm-settings">
          <h3>${this._t("npm.title")}</h3>
          <div class="npm-configured-layout">
          <div class="npm-details">
            <strong>${selectedLabel ? this._escape(selectedLabel) : this._t("npm.connected_short")}</strong>
            ${connectionDetails ? `<small>${this._escape(connectionDetails)}</small>` : ""}
            ${selectedLabel ? "" : `<span>${this._t("npm.no_exact_match")}</span>`}
            ${npm.last_error ? `<div class="error">${this._escape(npm.last_error)}</div>` : ""}
          </div>
          <div class="npm-controls">
            ${!selectedLabel ? `
              <div class="npm-select-row">
                <select id="npm-host" aria-label="${this._t("npm.select_host")}"><option value="">${this._t("npm.select_host")}</option>${hostOptions}</select>
                <button id="npm-select" ${this._busy ? "disabled" : ""}>${this._t("npm.select")}</button>
              </div>
            ` : ""}
            ${selectedLabel ? `
              <label class="check">
                <input id="npm-edge-protection" type="checkbox" ${npm.enabled ? "checked" : ""}>
                <span>
                  <strong>${this._t("npm.edge_protection")}</strong>
                  <small>${this._t("npm.edge_protection_hint")}</small>
                </span>
              </label>
              <label class="check">
                <input id="npm-protect-all-domains" type="checkbox" ${npm.protect_all_domains ? "checked" : ""}>
                <span>
                  <strong>${this._t("npm.protect_all_domains")}</strong>
                  <small>${this._t("npm.protect_all_domains_hint")}</small>
                </span>
              </label>
              ${this._data?.settings?.default_deny_enabled ? `
                <div class="allowed-region-warning">${this._t("npm.default_deny_active")}</div>
              ` : ""}
            ` : ""}
          </div>
          </div>
          <div class="npm-disconnect-row">
            <button class="danger" id="npm-disconnect" data-confirm="${this._escape(this._t("npm.disconnect_confirm"))}" ${this._busy ? "disabled" : ""}>${this._t("npm.disconnect")}</button>
          </div>
        </div>
        ${selectedLabel ? `
          <div class="npm-action-row npm-action-row-apply">
            <button class="primary" id="npm-apply" ${this._busy ? "disabled" : ""}>${this._t("apply")}</button>
          </div>
        ` : ""}
      </div>
    `;
  }

  _allowedRegionSection(settings) {
    return `
      <section class="allowed-regions-section">
        <h2>${this._t("allowed_regions.title")}</h2>
        <div class="body">
          <p class="hint">${this._t("region_list.hint")}</p>
          <div class="allowed-region-warning">${this._t("region_list.warning")}</div>
          ${this._allowedRegionControls(settings)}
        </div>
      </section>
    `;
  }

  _optionsSection(settings) {
    return `
      <section class="options-section">
        <h2>${this._t("options")}</h2>
        <div class="body">
          ${this._healthSummary(this._data?.status?.health)}
          <div class="options">
            ${this._checkbox("auto_ban_enabled", this._t("settings.auto_ban_enabled"), this._t("settings.auto_ban_enabled_hint"), settings.auto_ban_enabled)}
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
          <div class="threshold">
            <label><span>${this._t("settings.activity_history_hours")}</span>
              <input id="history-hours" type="number" min="1" max="168" step="1" required value="${Number(settings.activity_history_hours ?? 24)}">
            </label>
          </div>
          <p class="hint" style="margin-top:8px;font-size:13px">${this._t("settings.activity_history_hours_hint")}</p>
          <div class="advanced-title">${this._t("advanced")}</div>
          <div class="options">
            ${this._checkbox("allowlisted_logins_can_ban", this._t("settings.allowlisted_logins_can_ban"), this._t("settings.allowlisted_logins_can_ban_hint"), settings.allowlisted_logins_can_ban, true)}
            ${this._checkbox("default_deny_enabled", this._t("settings.default_deny_enabled"), this._t("settings.default_deny_enabled_hint"), settings.default_deny_enabled, true)}
          </div>
          <div class="actions">
            <button class="primary" id="save-options" data-save-options ${this._busy ? "disabled" : ""}>${this._t("apply")}</button>
          </div>
        </div>
      </section>
    `;
  }

  _notificationSection(settings) {
    return `<section class="notifications-section"><h2>${this._t("tabs.notifications")}</h2><div class="body">
      <div class="options">
        ${this._checkbox("ban_notifications_enabled", this._t("settings.ban_notifications_enabled"), this._t("settings.ban_notifications_enabled_hint"), settings.ban_notifications_enabled)}
        ${this._checkbox("allowlisted_login_notifications_enabled", this._t("settings.allowlisted_login_notifications_enabled"), this._t("settings.allowlisted_login_notifications_enabled_hint"), settings.allowlisted_login_notifications_enabled)}
      </div>
      <div class="subsection"><h3>${this._t("troubleshooting")}</h3>
        ${this._checkbox("blocked_request_logging_enabled", this._t("settings.blocked_request_logging_enabled"), this._t("settings.blocked_request_logging_enabled_hint"), settings.blocked_request_logging_enabled)}
      </div>
      <div class="actions"><button class="primary" data-save-options ${this._busy ? "disabled" : ""}>${this._t("apply")}</button></div>
    </div></section>`;
  }

  _allowedRegionControls(settings) {
    const local = this._data?.geoip?.local_region || {};
    const rules = settings.public_region_rules || {};
    const enabled = Boolean(settings.public_region_enabled);
    const country = local.country_code || "";
    const subdivision = local.subdivision_code || "";
    const rows = Object.entries(rules).map(([code, count]) => `
      <form class="region-entry" data-original-region="${this._escape(code)}">
        <label>${this._t("regional_rules.region")}<input name="region" required maxlength="9" pattern="[A-Za-z]{2}(-[A-Za-z0-9]{1,6})?" value="${this._escape(code)}"></label>
        <label>${this._t("regional_thresholds.attempts")}<input name="threshold" type="number" min="0" max="100" step="1" required value="${Number(count)}"></label>
        <button class="primary region-apply-unchanged" disabled>${this._t("apply")}</button>
        <button type="button" class="danger" data-action="remove_public_region" data-value="${this._escape(code)}" ${this._busy ? "disabled" : ""}>${this._t("remove")}</button>
      </form>
    `).join("");
    return `
      <div class="region-toggle-row">
        <label><input id="public-region-enabled" type="checkbox" ${enabled ? "checked" : ""} ${this._busy ? "disabled" : ""}> ${this._t("region_list.enabled")}</label>
        <button class="primary region-apply-unchanged" id="save-region-options" disabled>${this._t("apply")}</button>
      </div>
      <div class="region-shortcuts">
        ${country && !(country in rules) ? `<button data-region-code="${this._escape(country)}" ${this._busy ? "disabled" : ""}><ha-icon icon="mdi:plus" aria-hidden="true"></ha-icon>${this._t("region_list.add_country")} · ${this._escape(this._regionDisplay(local.country_name || this._countryName(country), country))}</button>` : ""}
        ${subdivision && !(subdivision in rules) ? `<button data-region-code="${this._escape(subdivision)}" ${this._busy ? "disabled" : ""}><ha-icon icon="mdi:plus" aria-hidden="true"></ha-icon>${this._t("region_list.add_subdivision")} · ${this._escape(this._subdivisionDisplay(local.subdivision_label, subdivision))}</button>` : ""}
      </div>
      ${rows ? `<div class="region-rows">${rows}</div>` : ""}
      <form id="regional-threshold-form">
        <label>${this._t("regional_rules.region")}<input name="region" required maxlength="9" pattern="[A-Za-z]{2}(-[A-Za-z0-9]{1,6})?" placeholder="CA / CA-NL" aria-label="${this._t("regional_rules.region")}"></label>
        <label>${this._t("regional_thresholds.attempts")}<input name="threshold" type="number" min="0" max="100" step="1" required value="${Number(settings.login_attempts_threshold || 0)}"></label>
        <button class="primary" ${this._busy ? "disabled" : ""}>${this._t("add")}</button>
      </form>
    `;
  }

  _shortSubdivisionCode(code) {
    return code && code.includes("-") ? code.split("-").pop() : code;
  }

  async _saveRegion(code, threshold, originalRegion) {
    const region = code.trim().toUpperCase();
    const rules = { ...this._data.settings.public_region_rules };
    if (originalRegion) delete rules[originalRegion];
    const country = region.split("-")[0];
    const overlaps = Object.keys(rules).filter(key => region.includes("-") ? key === country : key.startsWith(`${country}-`));
    overlaps.forEach(key => delete rules[key]);
    rules[region] = threshold;
    const enabled = this._data.settings.public_region_enabled;
    const explanation = overlaps.length
      ? this._t(region.includes("-") ? "region_list.replace_country" : "region_list.replace_provinces", { country: this._regionDisplay(this._countryName(country), country), region })
      : "";
    if ((overlaps.length || enabled) && !await this._confirm(`${explanation ? `${explanation}\n\n` : ""}${this._regionConfirmation(rules, enabled)}`)) return;
    this._post("set_public_region", { value: region, threshold, ...(originalRegion ? { original_region: originalRegion } : {}), replace_regions: overlaps, confirmed: true });
  }

  _regionConfirmation(rules, enabled) {
    const local = this._data?.geoip?.local_region || {};
    const rows = Object.entries(rules).map(([code, count]) => {
      const name = code.includes("-")
        ? code === local.subdivision_code
          ? this._subdivisionDisplay(local.subdivision_label, code)
          : code
        : this._regionDisplay(this._countryName(code), code);
      return `${name}: ${count} ${this._t("regional_thresholds.attempts")}`;
    });
    return `${this._t("apply")}?\n\n${this._t("allowed_regions.title")}\n${enabled ? rows.join("\n") : this._t("allowed_regions.anywhere")}\n\n${this._t(enabled ? "region_list.warning" : "allowed_regions.anywhere_hint")}`;
  }

  _subdivisionDisplay(name, code) {
    const shortCode = this._shortSubdivisionCode(code);
    if (name && shortCode && name !== shortCode) {
      return `${name} (${shortCode})`;
    }
    return name || shortCode || code || "";
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
            <button class="primary" data-action="export_config" ${this._busy ? "disabled" : ""}>${this._t("backup.save")}</button>
            <button
              class="secondary"
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
            <button class="primary" data-action="download_config" ${this._busy ? "disabled" : ""}>${this._t("backup.download")}</button>
            <button class="secondary" data-action="upload_config" ${this._busy ? "disabled" : ""}>${this._t("backup.upload")}</button>
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
        <input type="checkbox" role="switch" data-option="${key}" ${checked ? "checked" : ""} ${this._busy ? "disabled" : ""}>
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
            <button class="danger" data-action="${removeAction}" data-value="${this._escape(row.value)}" aria-label="${this._escape(`${this._t("remove")} ${row.label}`)}"${confirmAttr} ${this._busy ? "disabled" : ""}>${this._t("remove")}</button>
          </div>
        `).join("")}
      </div>
    `;
  }

  _bindApplyState(button, controls, changed) {
    const update = () => { button.disabled = Boolean(this._busy) || !changed(); };
    controls.forEach(control => {
      control.addEventListener("input", update);
      control.addEventListener("change", update);
    });
    update();
  }

  _wireEvents(root = this.shadowRoot) {
    root.querySelectorAll("button[data-panel-tab]").forEach((button) => {
      button.addEventListener("click", () => this._selectPanelTab(button.dataset.panelTab));
      button.addEventListener("keydown", (event) => {
        const tabs = [...this.shadowRoot.querySelectorAll("button[data-panel-tab]")];
        const index = tabs.indexOf(button);
        const next = { ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1 }[event.key];
        if (next === undefined) return;
        event.preventDefault();
        this._selectPanelTab(tabs[next].dataset.panelTab);
        tabs[next].focus();
      });
    });
    root.querySelectorAll("button[data-activity-view]").forEach((button) => {
      button.addEventListener("click", () => {
        const view = button.dataset.activityView === "history" ? "history" : "recent";
        this._showActivityView(view, true);
      });
      button.addEventListener("keydown", event => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const view = event.key === "Home" ? "recent" : event.key === "End" ? "history" : button.dataset.activityView === "history" ? "recent" : "history";
        this._showActivityView(view, true);
      });
    });
    const npmConnect = root.getElementById("npm-connect-form");
    if (npmConnect) {
      npmConnect.addEventListener("submit", (event) => {
        event.preventDefault();
        this._post("npm_connect", {
          base_url: root.getElementById("npm-url")?.value || "",
          identity: root.getElementById("npm-identity")?.value || "",
          secret: root.getElementById("npm-secret")?.value || "",
        });
      });
    }
    root.querySelectorAll("form[data-action]").forEach((form) => {
      if (form.dataset.action === "add_allowlist") {
        const input = form.querySelector('input[name="value"]');
        this._bindApplyState(form.querySelector("button"), [input], () => Boolean(input.value.trim()));
      }
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        const value = new FormData(form).get("value");
        if (form.dataset.action === "add_allowlist" && (this._busy || !String(value || "").trim())) return;
        if (value) {
          this._post(form.dataset.action, { value }).then((ok) => {
            if (ok) {
              form.reset();
            }
          });
        }
      });
    });
    root.querySelectorAll("[data-action]:not(form)").forEach((button) => {
      button.addEventListener("click", async () => {
        if (button.dataset.action === "remove_public_region") {
          const rules = { ...this._data.settings.public_region_rules };
          delete rules[button.dataset.value];
          const enabled = this._data.settings.public_region_enabled;
          if (enabled && Object.keys(rules).length === 0) {
            this._post("remove_public_region", { value: button.dataset.value, confirmed: true });
            return;
          }
          this._post("remove_public_region", { value: button.dataset.value, confirmed: true });
          return;
        }
        if (button.dataset.action === "upload_config") {
          const upload = root.getElementById("backup-upload");
          if (upload) {
            upload.click();
          }
          return;
        }
        if (button.dataset.confirm && !await this._confirm(button.dataset.confirm)) {
          return;
        }
        this._post(button.dataset.action, { value: button.dataset.value, confirmed: Boolean(button.dataset.confirm) });
      });
    });
    const uploadInput = root.getElementById("backup-upload");
    if (uploadInput) {
      uploadInput.addEventListener("change", async () => {
        const file = uploadInput.files && uploadInput.files[0];
        uploadInput.value = "";
        if (!file) {
          return;
        }
        if (
          !await this._confirm(
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
    const npmSelect = root.getElementById("npm-select");
    if (npmSelect) {
      npmSelect.addEventListener("click", () => {
        const hostId = root.getElementById("npm-host")?.value || "";
        if (hostId) {
          this._post("npm_select_host", { host_id: hostId });
        }
      });
    }
    const npmApply = root.getElementById("npm-apply");
    if (npmApply) {
      this._bindApplyState(npmApply, [root.getElementById("npm-edge-protection"), root.getElementById("npm-protect-all-domains")].filter(Boolean), () =>
        Boolean(root.getElementById("npm-edge-protection")?.checked) !== Boolean(this._data?.nginx_proxy_manager?.enabled)
        || Boolean(root.getElementById("npm-protect-all-domains")?.checked) !== Boolean(this._data?.nginx_proxy_manager?.protect_all_domains));
      npmApply.addEventListener("click", async () => {
        const enabled = Boolean(
          root.getElementById("npm-edge-protection")?.checked
        );
        const protectAllDomains = Boolean(
          root.getElementById("npm-protect-all-domains")?.checked
        );
        if (
          protectAllDomains &&
          !this._data?.nginx_proxy_manager?.protect_all_domains &&
          !await this._confirm(this._t("npm.protect_all_domains_warning"))
        ) {
          return;
        }
        if (
          enabled &&
          this._data?.settings?.default_deny_enabled &&
          !await this._confirm(this._t("npm.mirror_default_deny_warning"))
        ) {
          return;
        }
        this._post("set_options", {
          options: {
            npm_edge_protection_enabled: enabled,
            npm_protect_all_domains: protectAllDomains,
          },
        });
      });
    }
    const npmDisconnect = root.getElementById("npm-disconnect");
    if (npmDisconnect) {
      npmDisconnect.addEventListener("click", async () => {
        if (
          !npmDisconnect.dataset.confirm ||
          await this._confirm(npmDisconnect.dataset.confirm)
        ) {
          this._post("npm_disconnect");
        }
      });
    }
    root.querySelectorAll("[data-save-options]").forEach(button => {
      const section = button.closest("section");
      const controls = [...section.querySelectorAll("input[data-option], input[type=number]")];
      this._bindApplyState(button, controls, () => controls.some(input =>
        input.type === "checkbox" ? input.checked !== input.defaultChecked : Number(input.value) !== Number(input.defaultValue)));
      button.addEventListener("click", () => {
      if (![...section.querySelectorAll('input[type="number"]')].every(input => input.reportValidity())) return;
      this._post("set_options", { options: this._optionValues(section) });
      });
    });
    const regionToggle = root.getElementById("public-region-enabled");
    const regionApply = root.getElementById("save-region-options");
    regionToggle?.addEventListener("change", () => {
      const changed = regionToggle.checked !== Boolean(this._data.settings.public_region_enabled);
      regionApply.classList.toggle("region-apply-unchanged", !changed);
      regionApply.disabled = this._busy || !changed;
    });
    regionApply?.addEventListener("click", async () => {
      const enabled = root.getElementById("public-region-enabled").checked;
      if (enabled && !this._data.settings.public_region_enabled && !await this._confirm(this._regionConfirmation(this._data.settings.public_region_rules, true))) return;
      this._post("set_options", { options: { public_region_enabled: enabled, confirmed: true } });
    });
    const regionForm = root.getElementById("regional-threshold-form");
    root.querySelectorAll("form.region-entry").forEach((form) => {
      const apply = form.querySelector("button.primary");
      const changed = () => form.elements.region.value.trim().toUpperCase() !== form.dataset.originalRegion
        || form.elements.threshold.value === ""
        || Number(form.elements.threshold.value) !== Number(this._data.settings.public_region_rules[form.dataset.originalRegion]);
      form.addEventListener("input", () => {
        const dirty = changed();
        apply.classList.toggle("region-apply-unchanged", !dirty);
        apply.disabled = this._busy || !dirty;
      });
      form.addEventListener("submit", (event) => {
        event.preventDefault();
        if (!changed() || this._busy) return;
        if (!form.reportValidity()) return;
        this._saveRegion(form.elements.region.value, Number(form.elements.threshold.value), form.dataset.originalRegion);
      });
    });
    const addRegion = (code) => {
      const input = regionForm.elements.threshold;
      if (!input.reportValidity()) return;
      return this._saveRegion(code, Number(input.value));
    };
    regionForm?.addEventListener("submit", (event) => {
      event.preventDefault();
      addRegion(regionForm.elements.region.value);
    });
    root.querySelectorAll("[data-region-code]").forEach((button) => {
      button.addEventListener("click", () => addRegion(button.dataset.regionCode));
    });
  }

  _captureFormDrafts() {
    const drafts = [];
    this.shadowRoot?.querySelectorAll("section input, section select").forEach(input => {
      if (input.type === "file") return;
      const checkbox = ["checkbox", "radio"].includes(input.type);
      const dirty = input.tagName === "SELECT" ? [...input.options].some(option => option.selected !== option.defaultSelected) : checkbox ? input.checked !== input.defaultChecked : input.value !== input.defaultValue;
      if (!dirty) return;
      const form = input.closest("form");
      const section = input.closest("section").classList[0];
      let selector = input.id ? `#${CSS.escape(input.id)}` : input.dataset.option ? `[data-option="${CSS.escape(input.dataset.option)}"]` : null;
      if (!selector && input.name && form) {
        const parent = form.id ? `#${CSS.escape(form.id)}` : form.dataset.originalRegion ? `form[data-original-region="${CSS.escape(form.dataset.originalRegion)}"]` : form.dataset.action ? `form[data-action="${CSS.escape(form.dataset.action)}"]` : null;
        if (parent) selector = `${parent} [name="${CSS.escape(input.name)}"]`;
      }
      if (selector) drafts.push({ section, selector, checkbox, value: checkbox ? input.checked : input.value, option: input.dataset.option || (input.id === "threshold" ? "login_attempts_threshold" : input.id === "history-hours" ? "activity_history_hours" : null) });
    });
    return drafts;
  }

  _restoreFormDrafts(drafts, excludeSection = null, excludeOptions = []) {
    drafts.forEach(draft => {
      if (draft.section === excludeSection || excludeOptions.includes(draft.option)) return;
      const input = this.shadowRoot?.querySelector(`.${CSS.escape(draft.section)} ${draft.selector}`);
      if (!input) return;
      if (draft.checkbox) input.checked = draft.value;
      else input.value = draft.value;
      input.dispatchEvent(new Event(draft.checkbox ? "change" : "input", { bubbles: true }));
    });
  }

  _captureOptionDrafts() {
    const drafts = new Map();
    this.shadowRoot?.querySelectorAll("input[data-option], #threshold, #history-hours").forEach(input => {
      const checkbox = input.type === "checkbox";
      if (checkbox ? input.checked !== input.defaultChecked : input.value !== input.defaultValue) {
        drafts.set(input.dataset.option || (input.id === "history-hours" ? "activity_history_hours" : "login_attempts_threshold"), checkbox ? input.checked : input.value);
      }
    });
    return drafts;
  }

  _restoreOptionDrafts(drafts) {
    this.shadowRoot?.querySelectorAll("input[data-option], #threshold, #history-hours").forEach(input => {
      const key = input.dataset.option || (input.id === "history-hours" ? "activity_history_hours" : "login_attempts_threshold");
      if (!drafts.has(key)) return;
      if (input.type === "checkbox") input.checked = drafts.get(key);
      else input.value = drafts.get(key);
      input.dispatchEvent(new Event(input.type === "checkbox" ? "change" : "input", { bubbles: true }));
    });
  }

  _optionValues(root = this.shadowRoot) {
    const options = {};
    root.querySelectorAll("input[data-option]").forEach((input) => {
      options[input.dataset.option] = input.checked;
    });
    const threshold = root.querySelector("#threshold");
    if (threshold) options.login_attempts_threshold = Number(threshold.value || 0);
    const historyHours = root.querySelector("#history-hours");
    if (historyHours) options.activity_history_hours = Number(historyHours.value);
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
    const dateFormat = this._hass?.locale?.date_format;
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
      const formatter = new Intl.DateTimeFormat(locale, options);
      if (!dateFormat || !["DMY", "MDY", "YMD"].includes(dateFormat)) {
        return formatter.format(date);
      }

      const parts = formatter.formatToParts(date);
      const values = Object.fromEntries(
        parts
          .filter((part) => ["day", "month", "year"].includes(part.type))
          .map((part) => [part.type, part.value])
      );
      const separator =
        parts.find((part) => part.type === "literal" && /[^\s]/.test(part.value))
          ?.value.trim() || "/";
      const order = {
        DMY: ["day", "month", "year"],
        MDY: ["month", "day", "year"],
        YMD: ["year", "month", "day"],
      };
      const datePart = order[dateFormat]
        .map((part) => values[part])
        .join(separator);
      const timePart = new Intl.DateTimeFormat(locale, {
        hour: "2-digit",
        minute: "2-digit",
        ...(timeZone ? { timeZone } : {}),
        ...(options.hour12 === undefined ? {} : { hour12: options.hour12 }),
      }).format(date);
      return `${datePart}, ${timePart}`;
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
    return active?.tagName === "INPUT" || active?.tagName === "SELECT";
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

if (!customElements.get(PANEL_ELEMENT_NAME)) {
  customElements.define(PANEL_ELEMENT_NAME, IPBanManagerPanel);
}

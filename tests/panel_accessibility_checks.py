"""Accessibility checks for the server's isolated native HA UI validation runner.

Pass an authenticated Playwright page containing the preview fixture. No requests
are sent to the live Home Assistant instance; mutation tests use a stub API.
"""

import json
from pathlib import Path


def _density_fields(page):
    """Measure the text, retry, and activity-tab controls without editing them."""
    result = []
    for tab, selector in [
        ("allowed", 'form[data-action="add_allowlist"] input'),
        ("blocking", ".region-entry input, #regional-threshold-form input"),
        ("settings", "#threshold, #history-hours"),
        ("activity", ".activity-tabs button"),
    ]:
        page.locator(f'[data-panel-tab="{tab}"]').click()
        for control in page.locator(selector).all():
            result.append(control.bounding_box())
    page.locator('[data-panel-tab="allowed"]').click()
    return result


def _check_density(page):
    """Check mouse and touch dimensions after completing mouse interactions."""
    page.emulate_media(reduced_motion="reduce")
    page.set_viewport_size({"width": 1440, "height": 1000})
    page.locator('[data-panel-tab="allowed"]').click()
    remove = page.locator('ha-button[data-action="remove_allowlist"]').first
    add = page.locator('form[data-action="add_allowlist"] button')
    mouse_sizes = [control.bounding_box() for control in [remove, add]]
    assert all(size["width"] == 32 for size in mouse_sizes), mouse_sizes
    remove.hover()
    assert page.locator("#command-tooltip").is_visible()
    assert page.locator("#command-tooltip").text_content() == "Remove"
    page.locator("#command-tooltip").hover()
    page.wait_for_timeout(200)
    assert page.locator("#command-tooltip").is_visible()
    assert "Remove" in remove.aria_snapshot()
    for tab in page.locator("[data-panel-tab]").all():
        assert tab.text_content().strip()
        assert tab.locator(".command-label").count() == 0
    remove.evaluate("(el)=>el.focus()")
    assert page.locator("#command-tooltip").is_visible()
    page.keyboard.press("Escape")
    assert page.locator("#command-tooltip").is_hidden()
    mouse_fields = _density_fields(page)
    assert all(size["height"] == 32 for size in mouse_fields), mouse_fields
    assert all(size["height"] == 32 for size in mouse_sizes), mouse_sizes
    mouse_row = page.locator("#page-allowed .row").first.bounding_box()["height"]
    session = page.context.new_cdp_session(page)
    try:
        session.send("Emulation.setTouchEmulationEnabled", {"enabled": True})
        page.wait_for_timeout(100)
        touch_sizes = [control.bounding_box() for control in [remove, add]]
        touch_fields = _density_fields(page)
        assert all(size["height"] == 44 for size in touch_fields), touch_fields
        assert all(size["height"] == 44 for size in touch_sizes), touch_sizes
        assert all(size["width"] > 32 for size in touch_sizes), touch_sizes
        assert (
            remove.locator(".command-label").evaluate(
                "(el)=>getComputedStyle(el).position"
            )
            != "absolute"
        )
        assert (
            page.locator("#page-allowed .row").first.bounding_box()["height"]
            > mouse_row
        )
    finally:
        session.send("Emulation.setTouchEmulationEnabled", {"enabled": False})
        session.detach()
    print(
        "Adaptive sizes, desktop icon actions/tooltips, and touch text labels passed.",
        flush=True,
    )


def check(page):
    """Check contrast, semantics, keyboard interactions, and responsive reflow."""
    page.emulate_media(reduced_motion="reduce")
    page.add_script_tag(
        path="/root/codex-work/ipbm-ui-validation/tooling/node_modules/axe-core/axe.min.js"
    )
    findings = []
    measurements = {}
    tabs = ["activity", "allowed", "blocking", "notifications", "settings"]
    for theme in ["dark", "light"]:
        page.evaluate(
            """theme => {
          const el = document.createElement('style');
          el.textContent = theme === 'light'
            ? `html{--primary-background-color:#fafafa;--card-background-color:#fff;
                --secondary-background-color:#f0f0f0;--primary-text-color:#212121;
                --secondary-text-color:#606060;--divider-color:#ddd;color-scheme:light}`
            : `html{--primary-background-color:#111;--card-background-color:#1c1c1c;
                --secondary-background-color:#262626;--primary-text-color:#eee;
                --secondary-text-color:#aaa;--divider-color:#383838;color-scheme:dark}`;
          document.head.append(el);
        }""",
            theme,
        )
        page.set_viewport_size({"width": 1440, "height": 1000})
        for tab in tabs:
            page.locator(f'[data-panel-tab="{tab}"]').click()
            assert page.locator(".tab-page:visible").count() == 1
            width = page.locator(f"#page-{tab}").bounding_box()["width"]
            sections = page.locator(f"#page-{tab} > section").all()
            if tab != "activity":
                assert all(
                    section.bounding_box()["width"] < width * 0.6
                    for section in sections
                ), tab
            else:
                assert sections[0].bounding_box()["width"] > width * 0.95
            for selector in [
                "section",
                ".row",
                ".activity-row",
                ".region-entry",
                ".geoip-status",
            ]:
                candidates = page.locator(f"#page-{tab}").locator(selector)
                if not candidates.count():
                    continue
                page.locator('[data-panel-tab="activity"]').focus()
                page.mouse.move(0, 0)
                item = candidates.first
                before = item.evaluate("(el)=>getComputedStyle(el).outlineColor")
                bounds = item.bounding_box()
                item.hover(position={"x": 10, "y": 10})
                assert (
                    item.evaluate("(el)=>getComputedStyle(el).outlineColor") != before
                ), (tab, selector)
                assert (
                    item.evaluate("(el)=>getComputedStyle(el).cursor") != "pointer"
                ), (tab, selector)
                after = item.bounding_box()
                assert (bounds["width"], bounds["height"]) == (
                    after["width"],
                    after["height"],
                )
            page.wait_for_timeout(100)
            if tab in ["notifications", "settings"]:
                page.locator('[data-panel-tab="activity"]').focus()
                option = page.locator(f"#page-{tab} label.check:not(.risky)").first
                toggle = option.locator("ha-switch")
                assert (
                    option.evaluate("(el)=>getComputedStyle(el).borderTopWidth")
                    == "1px"
                )
                assert (
                    option.evaluate("(el)=>getComputedStyle(el).borderRadius") == "6px"
                )
                page.mouse.move(0, 0)
                before = option.evaluate("(el)=>getComputedStyle(el).outlineColor")
                toggle.hover()
                assert (
                    option.evaluate("(el)=>getComputedStyle(el).outlineColor") != before
                )
                assert (
                    toggle.evaluate("(el)=>getComputedStyle(el).outlineStyle") == "none"
                )
            result = page.evaluate("""async () => {
              const result = await axe.run(preview, {runOnly:{type:'tag',
                values:['wcag2a','wcag2aa','wcag21aa','wcag22aa','best-practice']}});
              return result.violations.map(v=>({id:v.id,impact:v.impact,
                nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary,html:n.html}))}));
            }""")
            findings.extend({"theme": theme, "tab": tab, **v} for v in result)
            measurements[f"{theme}-{tab}"] = page.locator(f"#page-{tab}").evaluate(
                "(el)=>Math.round(el.getBoundingClientRect().height)"
            )
    root = Path("/root/codex-work/ipbm-ui-validation/artifacts")
    (root / "accessibility-findings.json").write_text(json.dumps(findings, indent=2))
    (root / "compact-heights.json").write_text(json.dumps(measurements, indent=2))
    summary = [
        {"theme": item["theme"], "tab": item["tab"], "rule": item["id"]}
        for item in findings
    ]
    print("Accessibility findings:", summary, flush=True)
    print("Section heights:", measurements, flush=True)
    assert not findings, summary

    # Keyboard navigation, modal focus, and focus restoration use actual keys.
    page.set_viewport_size({"width": 1440, "height": 1000})
    settings = page.locator('[data-panel-tab="settings"]')
    settings.focus()
    page.keyboard.press("ArrowRight")
    assert (
        page.locator('[data-panel-tab="activity"]').get_attribute("aria-selected")
        == "true"
    )
    page.keyboard.press("Home")
    assert (
        page.locator('[data-panel-tab="activity"]').get_attribute("aria-selected")
        == "true"
    )
    page.keyboard.press("End")
    assert (
        page.locator('[data-panel-tab="settings"]').get_attribute("aria-selected")
        == "true"
    )
    page.evaluate("""() => {
      window.confirmationResult = null;
      preview._confirm('Accessibility review confirmation.').then(value => window.confirmationResult = value);
    }""")
    dialog = page.get_by_role("dialog", name="Confirm change")
    if not dialog.count():
        dialog = page.locator(".confirmation-dialog")
    dialog.wait_for()
    assert "Accessibility review confirmation." in dialog.aria_snapshot()
    assert page.locator('[data-choice="cancel"]').evaluate(
        '(el)=>el.matches(":focus-within") || el === el.getRootNode().activeElement'
    )
    for _ in range(4):
        page.keyboard.press("Tab")
        assert page.evaluate(
            'preview.shadowRoot.activeElement.closest(".confirmation-dialog") !== null'
        )
    page.keyboard.press("Escape")
    page.wait_for_function("window.confirmationResult === false")
    assert page.locator('[data-panel-tab="settings"]').evaluate(
        "(el)=>el === el.getRootNode().activeElement"
    )

    page.locator('[data-panel-tab="settings"]').click()
    switch = page.locator("#page-settings ha-switch[data-option]").first
    original = switch.evaluate("(el)=>el.checked")
    switch.locator("input").focus()
    page.keyboard.press("Space")
    page.wait_for_function("!preview._busy")
    assert switch.evaluate("(el)=>el.checked") != original
    switch.locator("input").focus()
    page.keyboard.press("Space")
    page.wait_for_function("!preview._busy")
    assert switch.evaluate("(el)=>el.checked") == original
    assert (
        switch.evaluate('(el)=>getComputedStyle(el.closest("label")).outlineWidth')
        == "2px"
    )
    assert switch.evaluate("(el)=>getComputedStyle(el).outlineStyle") == "none"
    assert "switch" in switch.aria_snapshot()
    print("Switch accessibility tree:", switch.aria_snapshot(), flush=True)

    page.locator('[data-panel-tab="notifications"]').click()
    page.locator(".logging-section ha-switch").click()
    assert page.locator("#page-notifications [data-save-options]").count() == 0
    page.wait_for_function("!preview._busy")
    assert page.evaluate("Object.keys(window.writes.at(-1).options)") == [
        "blocked_request_logging_enabled"
    ]
    page.locator('[data-panel-tab="settings"]').click()
    assert page.locator('[data-panel-tab="backup"]').count() == 0
    assert page.locator("#page-settings .backup-stack > .geoip-status").count() == 2
    risky = page.locator('[data-option="allowlisted_logins_can_ban"]')
    risky.click()
    page.locator('[data-choice="cancel"]').click()
    assert risky.evaluate("(el)=>!el.checked")
    risky.click()
    page.locator('[data-choice="confirm"]').click()
    page.wait_for_function("!preview._busy")
    assert risky.evaluate("(el)=>el.checked")
    risky.click()
    page.wait_for_function("!preview._busy")

    page.locator('[data-panel-tab="blocking"]').click()
    region_toggle = page.locator("#public-region-enabled")
    assert page.locator("#save-region-options").count() == 0
    region_toggle.click()
    page.locator('[data-choice="cancel"]').click()
    assert region_toggle.evaluate("(el)=>!el.checked")
    region_toggle.click()
    page.locator('[data-choice="confirm"]').click()
    page.wait_for_function("!preview._busy")
    assert region_toggle.evaluate("(el)=>el.checked")
    assert page.evaluate("window.writes.at(-1).options.confirmed") is True
    region_toggle.click()
    page.wait_for_function("!preview._busy")

    page.locator('[data-panel-tab="notifications"]').click()
    notification = page.locator('[data-option="ban_notifications_enabled"]')
    saved = notification.evaluate("(el)=>el.checked")
    page.evaluate("""()=>{window.toggleApi=preview._api;window.failedSaves=0;
      preview._api=async(...args)=>{
        if(args[0]!=='POST')return window.toggleApi(...args);
        window.failedSaves++;throw new Error('Test save failure.');};}""")
    notification.click()
    page.wait_for_function("!preview._busy")
    assert notification.evaluate("(el)=>el.checked") == saved
    assert page.evaluate("window.failedSaves") == 1
    page.evaluate(
        """()=>{preview._api=window.toggleApi;preview._toast='';preview._renderSafely();}"""
    )
    page.locator('[data-panel-tab="settings"]').click()

    # GeoIP hover and progress feedback, without downloading a database.
    disconnect = page.locator("#npm-disconnect")
    assert disconnect.locator(".command-icon").count() == 1
    before_disconnect = page.evaluate("window.writes.length")
    disconnect.click()
    page.locator('[data-choice="cancel"]').click()
    assert page.evaluate("window.writes.length") == before_disconnect
    page.evaluate(
        """()=>{window.savedNpmView=structuredClone(preview._data.nginx_proxy_manager);
      preview._data.nginx_proxy_manager.proxy_host_id=null;preview._renderSafely();}"""
    )
    assert page.locator("#npm-select .command-icon").count() == 1
    page.evaluate(
        """()=>{preview._data.nginx_proxy_manager=window.savedNpmView;preview._renderSafely();}"""
    )
    update = page.locator('[data-action="update_geoip"]')
    page.mouse.move(0, 0)
    page.wait_for_timeout(180)
    signature = '(el)=>getComputedStyle(el.shadowRoot.querySelector("[part~=base]")).backgroundColor'
    before = update.evaluate(signature)
    update.hover()
    page.wait_for_timeout(180)
    assert update.evaluate(signature) != before
    assert (
        update.evaluate(
            '(el)=>getComputedStyle(el.shadowRoot.querySelector("[part~=base]")).cursor'
        )
        == "pointer"
    )
    page.evaluate("""()=>{
      window.geoipApi=preview._api;window.geoipRequests=0;
      preview._api=async()=>{window.geoipRequests++;
        await new Promise(resolve=>window.finishGeoip=resolve);return structuredClone(preview._data);};
    }""")
    update.click()
    assert page.locator('[data-action="update_geoip"]').evaluate(
        "(el)=>el.loading && el.disabled"
    )
    assert page.evaluate("window.geoipRequests") == 1
    assert page.locator("#toast").is_visible()
    page.evaluate("window.finishGeoip()")
    page.wait_for_function("!preview._busy")
    page.evaluate("preview._api=window.geoipApi")
    print(
        "GeoIP Update: visible hover/cursor feedback, one action, and pending spinner passed.",
        flush=True,
    )

    # Server rejection must retain input, focus the field, and expose its error.
    page.locator('[data-panel-tab="allowed"]').click()
    page.evaluate("""()=>{
      window.originalApi=preview._api;
      preview._api=async()=>{const error=new Error('Invalid test address.');error.status=400;throw error;};
    }""")
    address = page.locator('form[data-action="add_allowlist"] input')
    address.fill("not an address")
    page.locator('form[data-action="add_allowlist"] button').click()
    page.wait_for_function("!preview._busy && Boolean(preview._fieldError)")
    address = page.locator('form[data-action="add_allowlist"] input')
    assert address.input_value() == "not an address"
    assert address.get_attribute("aria-invalid") == "true"
    assert address.evaluate("(el)=>el === el.getRootNode().activeElement")
    assert (
        page.locator("#field-validation-error").text_content()
        == "Invalid test address."
    )
    address.fill("")
    assert address.get_attribute("aria-invalid") is None
    page.evaluate("""()=>{preview._api=window.originalApi;preview._fieldError=null;
          preview._toast='';preview._renderSafely();}""")

    # Autosave leaves typing/navigation responsive and coalesces pending edits.
    page.locator('[data-panel-tab="settings"]').click()
    page.locator("#threshold").fill("7")
    assert page.locator('.numeric-options input[type="number"]').count() == 2
    assert page.locator(".numeric-options button").count() == 0
    assert page.locator(".numeric-options ha-switch").count() == 0
    page.evaluate("""()=>{
      window.pendingWrites=0;window.pendingReleases=[];
      preview._api=async(method,path,body)=>{window.pendingWrites++;window.pendingOptions=body.options;
        const options={...body.options};
        await new Promise(resolve=>window.pendingReleases.push(resolve));
        const next=structuredClone(preview._data);Object.assign(next.settings,options);return next;};
    }""")
    page.locator("#threshold").press("Enter")
    page.wait_for_function("window.pendingWrites===1")
    assert page.locator("#threshold").is_enabled()
    page.locator("#threshold").fill("8")
    page.locator("#threshold").press("Enter")
    assert page.evaluate("window.pendingWrites") == 1
    assert sorted(page.evaluate("Object.keys(window.pendingOptions)")) == [
        "login_attempts_threshold",
    ]
    page.evaluate("window.pendingReleases.shift()()")
    page.wait_for_function("window.pendingWrites===2")
    page.evaluate("window.pendingReleases.shift()()")
    page.wait_for_function(
        "!preview._busy && preview._data.settings.login_attempts_threshold===8"
    )
    assert page.locator("#threshold").input_value() == "8"
    assert "8" in page.locator("#toast").text_content()
    assert (
        page.evaluate('preview._optionLabel("login_attempts_threshold")')
        in page.locator("#toast").text_content()
    )
    page.evaluate("""()=>{preview._api=window.originalApi;preview._renderSafely();}""")
    assert not page.evaluate("preview._hasUnsavedChanges()")
    page.locator("#history-hours").fill("36")
    page.locator('[data-panel-tab="activity"]').click()
    page.wait_for_function(
        "!preview._busy && preview._data.settings.activity_history_hours===36"
    )
    assert page.evaluate("preview._panelTab") == "activity"
    assert "36" in page.locator("#toast").text_content()
    assert (
        page.evaluate('preview._optionLabel("activity_history_hours")')
        in page.locator("#toast").text_content()
    )
    page.locator('[data-panel-tab="settings"]').click()
    writes = page.evaluate("window.writes.length")
    page.locator("#threshold").fill("999")
    page.locator("#threshold").press("Enter")
    assert page.locator("#threshold").get_attribute("aria-invalid") == "true"
    assert page.evaluate("preview._data.settings.login_attempts_threshold") == 8
    assert page.evaluate("window.writes.length") == writes
    page.locator("#threshold").fill("8")
    page.locator("#threshold").press("Enter")
    page.locator("#threshold").fill("9")
    assert page.evaluate(
        """()=>{const event=new Event('beforeunload',{cancelable:true});
          window.dispatchEvent(event);return event.defaultPrevented;}"""
    )
    page.locator("#threshold").fill("8")
    page.locator("#threshold").press("Enter")
    page.evaluate("preview._renderSafely()")
    page.evaluate("""()=>{window.numericFailures=0;
      preview._api=async(...args)=>{
        if(args[0]!=='POST')return window.originalApi(...args);
        window.numericFailures++;throw new Error('Test numeric failure.');};}""")
    page.locator("#threshold").fill("9")
    page.locator("#threshold").press("Enter")
    page.wait_for_function("window.numericFailures===1 && !preview._busy")
    assert page.locator("#threshold").input_value() == "8"
    assert page.evaluate("preview._data.settings.login_attempts_threshold") == 8
    assert page.evaluate("preview._toastType") == "error"
    assert (
        page.evaluate('preview._optionLabel("login_attempts_threshold")')
        in page.locator("#toast").text_content()
    )
    page.evaluate("preview._api=window.originalApi")
    page.locator("#threshold").fill("8")
    page.locator("#threshold").press("Enter")

    # 720 CSS pixels reproduces desktop 200% zoom reflow; 320 tests mobile.
    for direction in ["ltr", "rtl"]:
        page.evaluate(
            "direction=>{document.documentElement.dir=direction;preview.dir=direction;}",
            direction,
        )
        for width in [720, 320]:
            page.set_viewport_size({"width": width, "height": 700})
            for tab in tabs:
                page.locator(f'[data-panel-tab="{tab}"]').click()
                assert page.evaluate(
                    "document.documentElement.scrollWidth <= innerWidth + 1"
                ), (direction, width, tab)
                assert page.locator(f"#page-{tab}").evaluate(
                    "(el)=>el.scrollWidth <= el.clientWidth + 1"
                ), (direction, width, tab)
            settings = page.locator('[data-panel-tab="settings"]')
            settings.focus()
            page.keyboard.press("ArrowRight")
            expected = "notifications" if direction == "rtl" else "activity"
            assert (
                page.locator(f'[data-panel-tab="{expected}"]').get_attribute(
                    "aria-selected"
                )
                == "true"
            ), (direction, width, page.evaluate("getComputedStyle(preview).direction"))
            assert page.locator(f'[data-panel-tab="{expected}"]').evaluate("""el=>{
              const box=el.getBoundingClientRect(), view=el.parentElement.getBoundingClientRect();
              return box.left >= view.left - 2 && box.right <= view.right + 2;
            }"""), (direction, width)
    page.evaluate('document.documentElement.dir="ltr";preview.dir="ltr"')
    page.set_viewport_size({"width": 390, "height": 900})
    page.locator('[data-panel-tab="settings"]').click()
    menu = page.locator("#panel-menu ha-icon-button")
    menu.wait_for(state="visible")
    page.evaluate(
        """()=>{window.menuOpened=false;
          preview.addEventListener('hass-toggle-menu',()=>window.menuOpened=true,{once:true});}"""
    )
    menu.click()
    assert page.evaluate("window.menuOpened")
    page.keyboard.press("Escape")
    page.screenshot(path=str(root / "compact-accessible-mobile.png"))
    _check_density(page)
    print(
        "Keyboard, switch semantics, modal focus/escape, mobile menu, RTL, "
        "reduced motion, and 200% equivalent reflow passed.",
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit("Called by the isolated native UI validation runner.")

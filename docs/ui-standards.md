# Panel UI And UX Standards

Reviewed against Home Assistant's official contributor documentation on 2026-11-16.
This is development guidance, not a claim that every check below already passes.
Preserve the approved five-tab layout and compact appearance. Backup is a compact
two-row Settings section, not a separate tab. Do not raise the
Home Assistant 2026.4.0 minimum or deploy a change without approval.

## Components Before Custom Styling

- Prefer Home Assistant's `ha-*` components over drawing replacements. Do not
  introduce new `mwc-*` components or use Web Awesome components directly.
- Check each component and attribute against the minimum supported frontend;
  today's component documentation does not establish April 2026 compatibility.
- Use supported properties, theme variables, and slots rather than overriding
  component internals. `ha-button` provides appearance, variant, loading,
  disabled, and start/end icon slots.
- Actions use buttons, navigation uses links, and binary settings use switches.
  Switches save immediately. Login attempts and history retention save valid
  edits on blur or Enter; do not save while typing. Preserve drafts across
  updates and restore a switch's saved state after a rejected save.

Sources: [Components](https://contributors.home-assistant.io/frontend/architecture/components/),
[Buttons](https://contributors.home-assistant.io/design/components/button/).

## Theme And Visual Hierarchy

- Prefer semantic color tokens, matching foreground/background pairs, and theme
  fallbacks instead of hard-coded hex colors or arbitrary color mixing.
- Use HA typography, spacing, radius, and animation tokens with compatible
  fallbacks. HA spacing follows a four-pixel scale.
- Use restrained emphasis for the main action; warnings and destructive actions
  must remain distinct and understandable without color alone.
- HA's default button is pill-shaped. The approved compact rounded buttons are
  an intentional project customization; do not silently revert their shape.
- Polish must not add decorative dashboards, invented metrics, or larger branding.

Sources: [Foundations](https://contributors.home-assistant.io/design/foundations/),
[Color](https://contributors.home-assistant.io/design/foundations/color/),
[Typography](https://contributors.home-assistant.io/design/foundations/typography/),
[Spacing](https://contributors.home-assistant.io/design/foundations/spacing/).

## Input, Feedback, And Safety

- Keep visible labels, concise field-specific hints, and explicit required states.
- Show field errors beside the field, preserve the input, and focus the first
  invalid field. Toasts alone are not sufficient validation feedback.
- Give long-running actions a busy state and prevent duplicate submissions.
  Progress feedback must not change button dimensions or lose the user's focus.
- Render local content promptly and enrich it incrementally. Distinguish pending
  content from a genuinely empty result. Loading must end in content, an empty
  state, or an actionable error, never an indefinite spinner.
- Confirm genuinely risky changes, not routine reversible edits. Name the actual
  action in confirmation buttons; focus the safe choice for destructive actions.
- Preserve unsaved work and warn before navigation discards it. Do not add Undo
  for a security operation without a backend-safe, explicitly designed inverse.

Sources: [Forms](https://contributors.home-assistant.io/design/patterns/forms/),
[Loading](https://contributors.home-assistant.io/design/patterns/loading/),
[Confirmations](https://contributors.home-assistant.io/design/patterns/confirmations/).

## Accessibility And Mobile

- Verify keyboard-only use, visible unobscured focus, labels, status announcements,
  and focus restoration. Icon-only controls need accessible names and tooltips.
- Measure text contrast and essential control contrast in both themes; using an
  HA token is not proof that every foreground/background combination is suitable.
- Keep generous touch targets. WCAG AA's 24-pixel minimum is not the design goal;
  the existing approximately 44-pixel actions remain appropriate.
- Test 200% zoom, long translations, RTL layouts, narrow viewports, reduced motion,
  and a screen reader. A screenshot or overflow test alone does not certify these.
- A custom panel owns its mobile header/sidebar access. Verify that users can
  open the HA menu and reach the selected tab on a narrow screen.
- Use short, meaningful token-based transitions and respect reduced motion.

Sources: [Accessibility](https://contributors.home-assistant.io/design/foundations/accessibility/),
[Motion](https://contributors.home-assistant.io/design/foundations/motion/),
[Custom panels](https://contributors.home-assistant.io/frontend/custom-ui/panel/).

## Local Preview Accessibility Review

The preview retains the Home Assistant 2026.4.0 minimum. Native `ha-switch`,
`ha-button`, and `ha-menu-button` are checked against that frontend. Text fields
retain native HTML form validation and submission; confirmations use the browser
modal dialog with explicit safe initial focus, keyboard cycling, Escape, and
focus restoration. Do not replace working form semantics just for appearance.

The server-side browser checks in `tests/panel_accessibility_checks.py` cover:

- Axe WCAG A/AA and best-practice scans across all five tabs in both themes.
- Accessible switch names, descriptions, and states in Chromium's accessibility
  tree; real keyboard switch activation and tab navigation.
- Confirmation focus containment, cancellation, and restoration.
- Mobile sidebar access, selected-tab visibility, RTL arrow navigation,
  reduced-motion preference, and reflow at 320 and 720 CSS pixels. The latter
  models a 1440-pixel desktop viewport at 200% zoom, not every browser zoom mode.
- Rejected-field input preservation, accessible inline errors and focus,
  loading feedback, double-submit prevention, and unsaved-page-exit protection.

Contrast failures found in selected tabs, links, Add, and Remove controls were
corrected. The compact layout reduces section padding, heading height, row gaps,
and empty-state space. Action buttons use 32-pixel heights for precise mouse
input. Desktop actions with icons use compact square buttons and hover/focus
tooltips; touch-capable devices retain 44-pixel heights and visible text labels.
Tabs and confirmation buttons remain labeled. Text fields, retry inputs,
selectors, and Recent/History controls follow the same adaptive heights.
CSS capability queries adapt
immediately when input capability changes.
Wide Settings views use two natural-height columns; narrow views remain stacked.
Allowed IPs, Blocking, and Notifications also use two columns when space permits;
Activity alone remains full width. Subtle outlines identify hovered cards and
rows without making them clickable or changing their cursor. Focus within a row
provides corresponding keyboard emphasis without moving its contents.
Options, notification, NPM, and Public Region Lock switches save individually.
Risk-increasing changes retain confirmation; cancellation and failed writes
restore the saved switch state. Regional rule edits retain their explicit Apply;
the Settings numeric fields do not have an Apply button. They save on blur/Enter
without replacing the editor DOM or swallowing the user's navigation click.
Pending edits are coalesced and saved after an in-flight request completes.
Success toasts identify each changed setting and the server-confirmed value;
failed saves show an error rather than an applied message.

The permanent isolated browser runner is
`server:/root/codex-work/ipbm-native-ui-check.py`. Set `IPBM_A11Y=1` to invoke
these checks; `IPBM_TEST_URL` chooses the isolated instance. Axe is installed in
the server UI validation tooling directory, not shipped with the integration.

Automated scans and accessibility-tree inspection are not a claim of complete
WCAG conformance or an NVDA/VoiceOver user test. Audible screen-reader behavior
and Safari/Firefox-specific behavior have not been manually certified.

Implement incrementally on server, verify against the minimum HA version and a
current version, and install previews only with user approval. Keep all security
enforcement and authoritative validation in the backend.

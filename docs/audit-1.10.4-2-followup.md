# Second Independent Audit Follow-Up

Baseline: `39091e5933366e70057f3af9dc223650a2cd7efe` (v1.10.4-2).
All remediation and verification took place on Server. No release, GitHub write,
or installation on a user's Home Assistant is part of this work.

## Reproductions And Fixes

The initial seven regression cases failed on the baseline and passed after
remediation: three hexadecimal numeric DNS spellings, unexpected history-load
failure, failed cleanup marked complete, the options-flow GeoIP-disable gap,
and silent clamping of direct regional-threshold writes. See
`tests/ip_ban_manager/test_remaining_audit.py` and Server evidence under
`/root/codex-work/ipbm-independent-audit/remaining-*.log`.

| Audit Items | Disposition |
| --- | --- |
| NEW-M1 | Manual panel/service adds already normalize through the installed hook; the existing mapped-address regression passes. Added normalization before manual validation, on lookup construction for legacy stored keys, and when generating NGINX deny rules. |
| NEW-M2, NEW-L27 | Guarded the legacy options form and its save path; added a localized form error. Health checks now include disabled GeoIP required by region/threshold rules. Direct regional-threshold API writes reject out-of-range values. |
| NEW-M3 | Request-time MMDB cache misses remain synchronous because native HA ban membership is synchronous. Measured 10,000 uncached reader calls at 0.655 s total (0.066 ms mean) on Server. This is not Pi performance certification or an asynchronous-enforcement claim. Panel history policy filtering now runs in an executor. |
| NEW-M4 | Candidates must meet a size floor and successfully probe multiple public-IP records before replacement. A corrupt second probe preserves the previous file. No backup files are scattered into config. |
| NEW-M5 | Sanitized location PUT fields against upstream 2.15.1/2.17.0 schemas, retained newer location access-list IDs, and excluded NPM runtime metadata. Actual PUT/removal preservation checks passed on both isolated versions. |
| NEW-M6 | Documented the unavoidable GET/PUT race: NPM has no conditional-write API. Existing rollback checks are not described as atomic protection against all concurrent external edits. |
| NEW-M7 | Removing an allowed region while the lock is enabled now confirms the remaining region policy. The backend still refuses removal of the last enabled rule. |
| NEW-L1, NEW-L9 | Deferred health checks reschedule when startup is still in progress; lifetime invalidation prevents old health requests from writing after unload/reload. |
| NEW-L2, NEW-L8 | Cleanup is marked complete only after success. Executor cleanup is drained on cancellation. Startup callbacks and legacy migration tasks are tracked and cancelled/drained on unload. |
| NEW-L3 | Reproduced `panel.async_current_status is status.async_current_status` returning false after reload; it now returns true. Rebound imported functions/classes after the reload pass. Does not claim atomic hot replacement of every in-flight Python frame or arbitrary constant migration. |
| NEW-L4, NEW-L7 | Serialized vendored import-path changes and shared concurrent local-region enrichment. Kept the reader gate: the user explicitly requested these controls only when the database is available. |
| NEW-L5, NEW-L6 | Lazy reverse-DNS expiry with LRU promotion; history reads/saves trim the already-sorted buffer instead of re-sanitizing and sorting every event. |
| NEW-L10 | Cancelled previous DNS interval subscriptions on restart and tracked/drained active async resolution batches on unload. Cancelling async work does not claim to terminate an OS resolver call already running in an executor. No arbitrary new limit was imposed on existing user allowlists. |
| NEW-L11 | Unexpected storage exceptions set the history error state and prevent saving over unread stored history. Cancellation still propagates normally. |
| NEW-L12 through NEW-L18 | Region-row accessible names identify their region; warnings/errors have live-region roles; numeric errors survive rerenders and are placed outside the threshold container; region-toggle focus is visible; coarse-pointer toast-close targets are at least 44 px. |
| NEW-L14 | Translated add-on discovery, backup transfer, DNS placeholders, and the new flow error in every shipped non-English locale. Removed the two English-prose parity exemptions. |
| NEW-L19 | Rejected legacy hexadecimal numeric resolver spellings as DNS allowlist names. |
| NEW-L20 | Intentional scope: HA callback exceptions apply only to the selected HA host, not arbitrary Frigate/media domains. Existing README documents this; broadening it would weaken other hosts' policies. |
| NEW-L21, NEW-L22 | Numeric settings have independent save boundaries; late successes request reconciliation even when another foreground action has begun. |
| NEW-L23 | Not established as described: the active `_post` retains its panel instance and schedules queued numeric work from `finally`, including after disconnect. No speculative session-storage autosave mechanism was added. |
| NEW-L24 through NEW-L29 | Clear stored toasts before new actions; derive feedback from changed saved settings and stored regional thresholds; serialize region read/modify/write and notification option mutations; show history-hours pending feedback. |
| NEW-L30 through NEW-L39 | Handle null advanced config, preserve the original rollback error, distinguish permission denials from expired authentication, clarify sign-in failures, regenerate invalid saved secrets, use dedicated translated NPM Repairs, report manual sync failures without double-counting background handling, clear stale Repairs during restore, retry only transient token-renewal errors, and track failure origin explicitly. |
| NEW-L40, NEW-L41, NEW-I4 | Updated Renovate's Python constraint; ZIP validation compares file content as well as structure/version. Future asset generation includes SHA-256 and no longer silently clobbers existing assets. No existing release assets were changed. |
| NEW-L42 | Type stubs and runtime pins differ; this is a tooling-coverage qualification, not proof of a newer-only API defect. Minimum-version regression checks are recorded separately. |
| NEW-L43 | Incorrect: both workflows already use immutable SHAs. `master`/`main` occur only in version comments. |
| NEW-L44 | Added shareable GitHub evidence to the prior follow-up. PR 75 is merged at `c1914d3fbb4e551462ebe56111633b860ecafc28`; CI and release-upload evidence exists for the later pre-release. |

## Prior Findings And Intentional Boundaries

- M23: startup reconciliation no longer removes exact bans solely because a DNS
  hostname currently resolves to the address. Explicit configured IP/network
  allowances retain the original startup reconciliation behavior.
- M9: removing remote NPM rules requires valid NPM credentials. Reauthentication
  is the recovery path. No silent force-disconnect that leaves remote rules
  behind while claiming success was added.
- Callback exemptions, visible local defaults, one-click notification silencing,
  and entity attributes remain intentional product behavior. Removing them,
  adding mandatory confirmations to silence links, or changing entity access
  policy would change the agreed product rather than fix a demonstrated defect.
- NPM retries remain bounded in delay, not in total attempts, so protection can
  recover when a server returns after a long outage.
- NAT64, 6to4, and Teredo addresses are not blindly converted into IPv4 client
  identities. Encoded address bits alone do not establish the real peer.
- Browser verification uses isolated HA with sample data; its mutation stubs
  demonstrate UI behavior, not backend enforcement. Real HTTP/NPM exercises
  establish the separately listed enforcement and recovery results.

## Runtime Evidence

Real HA 2026.10.4 / NPM 2.17.0 checks passed for allowed/denied regional traffic,
allowlist changes, exact-ban persistence, failed-login thresholds, trusted-source
protection, direct access while NPM is stopped, automatic proxy recovery, and
disable/re-enable restoration of API, ten services, and enforcement.

NPM 2.15.1 and 2.17.0 preservation checks used the integration's real client and
verified user host fields and custom locations before update and after removal.
The Allowed IPs screenshot was regenerated from isolated sample data, not a user
installation. Automated native-frontend checks passed on the existing isolated
HA instance. This does not certify audible screen readers, every browser engine,
or performance on hardware not available for measurement.

## Final Checks

- Full Server suite: 534 passed, 258.83 seconds.
- HA 2026.4.0 targeted regression suite: 121 passed, 73.69 seconds.
- All panel JavaScript suites passed.
- Formatting, lint, type checking, JSON/YAML, and whitespace checks passed.
- Actual NPM preservation checks passed on isolated 2.15.1 and 2.17.0.
- Real integration exercise: `remaining-runtime-final.log`.
- Native frontend exercise: `remaining-browser-final.log` (sample-data UI writes
  are stubbed; real backend mutation verification is the separate HTTP exercise).

The remediation pass ended with uncommitted changes on Server and manifest
version 1.10.4-2. Later pre-release preparation versions and commits that work;
the evidence above describes the remediation snapshot. During that pass, no new
release assets were published and no user installation was changed. Future
release creation must leave ZIP/checksum upload to the workflow; attempts to
upload over an existing asset now fail instead of silently replacing it.

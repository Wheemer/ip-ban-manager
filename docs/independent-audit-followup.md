# Independent Audit Follow-Up

Work is local on Server. Nothing in this follow-up authorizes publication or
deployment to a user's Home Assistant.

## Runtime Changes

- Install ban and failed-login hooks before frontend registration and network
  discovery. Reconcile existing allowlisted bans after the hooks are active.
- Bound startup health suppression to 30 seconds, report a missing reader when
  region protection requires it, and prevent health refreshes surviving unload.
- Recognize NPM authentication failures by HTTP status, clear stale
  reauthentication state after successful authenticated calls, log sync failures,
  and create a Repair for persistent failures without stopping recovery retries.
- Preserve the complete editable NPM host payload. A real partial PUT reset
  `http2_support` in the isolated NPM instance; the revised client preserved user
  fields and custom locations in NPM 2.15.1 and 2.17.0.
- Include legacy access-list cleanup failures in the current host's rollback
  boundary. Wait for in-flight NPM policy transactions during unload.
- Reconcile option saves after errors and late completions. Keep mutation locks
  intact after rendering errors and avoid duplicate in-flight numeric writes.
- Show persistent numeric validation errors and a retry action after initial
  loading errors. Keep the backend's refusal to remove the last enabled region;
  do not claim that removing it automatically disables the lock.
- Validate downloaded MMDB files before atomic replacement, serialize downloads,
  and retain the previous database when a replacement is invalid.
- Bound and expire GeoIP result caches, including negative results; clear them
  when replacing or closing the reader. Handle corrupt-record decoding errors.
- Share bounded reverse-DNS work, cancel owned lookups on unload, and apply
  regional login thresholds before waiting for DNS enrichment.
- Bound DNS allowlist refresh concurrency and discard stale resolution results
  after configuration changes or unload.
- Normalize mapped and scoped IPv6 addresses for exact bans and removals; reject
  numeric IP typos rather than accepting them as DNS names.
- Share diagnostic status across the four sensors. Avoid sorting and parsing
  the complete activity history on each request, and preserve events received
  while stored history is loading.
- Reject malformed thresholds and unsafe imported NPM authorization secrets.
  Validate secrets again before generating NGINX configuration.
- Correct allowlist precedence wording and Activity translations in all locales.
  Record bundled-reader provenance and strengthen local ZIP validation.

## Additional Code Remediation

- Token renewal now uses the same failure logging and persistent Repair path as
  policy synchronization. Successful renewal rechecks an enabled policy instead
  of leaving a stale failure indefinitely.
- Pending NPM sign-in, host-selection, discovery, and activity requests are tied
  to their originating runtime. They cannot repopulate a replacement runtime
  after unload/reload.
- Serialized proxy actions also verify their original runtime after acquiring
  the policy lock, so queued actions cannot cross an unload/reload boundary.
- Earlier-host rollback verifies the host still matches the integration's last
  write, including its access list. Concurrent edits are reported, not erased.
- Failed or incomplete local-region enrichment is cached briefly rather than
  retried on every refresh or retained for twelve hours. Changing HA coordinates
  or country invalidates the cached location.
- Bulk ban replacement and backup restoration now share canonical-address and
  Home Assistant self-protection checks before writing bans.
- Setup fails clearly when native IP banning is disabled, instead of presenting
  an apparently loaded integration without enforcement.
- Option saves reject out-of-range thresholds and explain attempts to disable
  GeoIP while region controls still require it, rather than silently overriding
  the requested values.
- Reverse-DNS results must be valid hostnames. Integration-owned login logging
  strips query strings and control characters. Internal management and NPM
  authorization subrequests are not recorded as visitor activity.
- History deduplication uses a bounded key index, and storage-load failures are
  exposed in the History view. Malformed request paths cannot raise through
  activity sanitization. The Activity tabs have an accessible label and the page
  heading precedes its section headings without changing the visible branding.

## Audit Claims That Need Qualification

- PR 75 was merged; its squash commit is the audited commit. A linear git log
  does not establish that a PR was bypassed. The published ZIP exists.
  GitHub evidence: [PR 75](https://github.com/Wheemer/ip-ban-manager/pull/75)
  reports merge commit `c1914d3fbb4e551462ebe56111633b860ecafc28`.
  The subsequent pre-release's [asset upload](https://github.com/Wheemer/ip-ban-manager/actions/runs/43913085771)
  and [unit suite](https://github.com/Wheemer/ip-ban-manager/actions/runs/43911375743)
  both completed successfully for `39091e5933366e70057f3af9dc223650a2cd7efe`.
- Multi-host rollback already existed. The legacy-cleanup failure boundary was
  the narrower gap, not a complete absence of multi-host rollback.
- The backend already rejects removal of the last enabled region. The audit's
  claim that this silently disables region enforcement is incorrect.
- Callback exemptions, visible internal-network defaults, and one-click
  notification silencing are intentional product behavior. Do not reverse
  these decisions automatically in response to audit recommendations.
- GeoIP cache misses still perform synchronous MMDB decoding. Caching is a
  mitigation, not proof of zero event-loop cost under arbitrary source churn.

## Remaining Boundaries

This is not a declaration that all 85 audit entries are resolved. An explicit
force-disconnect flow, broader callback-policy changes, entity-attribute access
policy, and the hot-reload architecture require separate decisions or evidence.

## Real Runtime Verification

The existing isolated HA 2026.10.4 and NPM 2.17.0 instances on Server were
exercised through real HTTP APIs and NGINX authorization subrequests. Modern HA
proxy trust was configured in the isolated instance's HTTP storage, not in a
user installation. The source directory was mounted directly into isolated HA.

Verified: Canada allowed and a non-Canadian source denied; allowlist override
and removal; an exact ban persisted for a region-rejected address; automatic
ban at the fifth failed login; allowlisted failures not banned; direct HA API
access during NPM downtime; automatic proxy synchronization after recovery;
disabled API returning 404; ten services restored on re-enable; regional
enforcement restored after re-enable. Custom NPM host settings were preserved.

This exercise found an additional defect: the authorization cache retained old
decisions after policy changes. Cache entries now include the current policy,
exact-ban state, and reader identity; client addresses are normalized before
cache access. Actual allowlist override/removal passed with this correction.

Evidence: `/root/codex-work/ipbm-independent-audit/live-integration-final.log`.
The isolated instances were stopped afterwards. No user HA was changed.

The latest changes have not been rerun through the full suites. The runtime
exercise establishes the specific behaviors above, not exhaustive coverage of
every UI, backup, concurrency, or supported-version scenario. Do not describe
the entire tree as fully verified on the strength of earlier green runs.

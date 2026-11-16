# Independent audit follow-up: 1.10.4-3

This records the Server work prepared for 1.11.0 after the independent pre-release
audit. Live Home Assistant deployment is separate from release publication.

## Notification test discrepancy

The published test passed on Server and in the actual Unit Testing job log
(`43933835324`: 534 passed). This did not disprove the reviewer's failure.
The fixture did not allowlist its public address, so it delegated to HA's
`process_wrong_login`, which uses `gethostbyaddr` in an executor. The test's
mock of our external reverse-DNS function was not on that path.

A separate archive of the published tag reproduced both outcomes: with HA's
system resolver returning the expected provider name, the original test passes;
with a different name, the same original test fails. In both cases our mocked
lookup has zero awaits. Both reproduction cases passed in the diagnostic harness.
Thus the discrepancy is resolver-dependent, not a false-green CI run or an
environment-independent contradiction. The published test has message assertions,
not a mock-call-count assertion as described in the report.

The corrected fixture explicitly allowlists the public test address, so it now
exercises the intended allowlisted notification path using the mocked lookup.
No production notification behavior was changed to make the test pass.

## Confirmed changes

- GeoIP updates require every existing probe to succeed, preserve one previous
  database beside the managed database, and reopen/probe the installed candidate.
  A post-replacement failure restores the previous file. This is not a claim
  that a small sample can prove every record in a database is uncorrupted.
- NPM accepts a null location list as empty and omits null location identifiers.
  Unsupported response-field names are logged at debug level; nonempty locations
  with no supported fields are refused rather than silently erased.
- NPM distinguishes explicit expired-token bodies on HTTP 403 from ordinary
  permission errors. Policy and token failures recover independently; failed
  disable operations now use the same diagnostics as enable/sync.
- Enabling NPM without selecting a host is a configuration error, not a remote
  sync failure. Regenerated authorization secrets persist before reconciliation.
  If backup transaction recovery fails, any credentials or repaired secrets it
  wrote locally are rolled back to the original snapshot as well.
- Degenerate hexadecimal numeric resolver spellings are rejected. DNS resolutions
  use at most eight worker tasks rather than a task per hostname. Newly detected
  DNS/exact-ban conflicts log the hostname/address without deleting the ban.
- Local-region enrichment cannot restart after its unload drain, including a
  reader preparation that finishes after teardown begins.
- Panel feedback is limited to requested options, avoiding unrelated changes and
  legacy keys in toasts. Failed numeric values remain marked; independent success
  cannot overwrite their error toast. Fresh loads and changed values clear stale
  numeric errors. Detached loads release their busy state and resume queued saves.
- History pending feedback targets the actual nested label. Region icon tooltips
  use contextual accessible names. Region removal identifies the removed code;
  an enabled lock's sole remaining rule has a disabled Remove control.
- Activity location-cache expiry is lazy; reads no longer sweep the entire cache.

## Decisions and remaining evidence limits

- No silent force-disconnect is added: failed remote cleanup must not be presented
  as removed protection. Reauthenticate to remove remote rules; inaccessible NPM
  rules remain in place. This is an accepted recovery limitation, not full recovery
  from lost NPM credentials.
- The GeoIP-reader gate for home-region controls is intentional: the user requested
  those controls only when the database is available. No automatic external-IP
  geolocation is substituted for HA's configured home coordinates.
- No arbitrary cap is imposed on existing DNS allowlists. Eight workers bound task
  and lookup concurrency, but a very long list can still take multiple batches.
- Synchronous cache-miss decoding remains at the synchronous HA membership boundary.
  Server measurements are not Raspberry Pi benchmarks. No Pi performance claim is made.
- Python hot reload is not atomic namespace replacement. Function/class rebindings
  do not rewrite closure cells, already-created objects, or imported constant values.
- History's newest-first sanitized invariant is established by load/insert paths.
  Artificial in-memory corruption is not evidence of a reachable production writer.
  Grouping at most 200 summaries is retained instead of adding an unmeasured cache.
- NPM cannot guarantee atomicity against a concurrent external GET/PUT editor.
  Callback exemptions apply only to the selected HA host, not unrelated domains.
- Real NPM preservation/removal checks were rerun on the isolated 2.15.1 and 2.17
  installations. This does not certify every historical NPM 2.x schema.
- Node tests are not browser, screen-reader, Safari, or Firefox certification.
  No new browser/version-specific certification is claimed by this follow-up.

## Reproduction evidence on Server

Evidence directory: `/root/codex-work/ipbm-independent-audit/`.

- `audit-1.10.4-3.md`: received independent report.
- `repro-3-baseline/`: archived published source and diagnostic DNS fixture probe.
- `prerelease-3-fixture-discrepancy.log`: both controlled resolver outcomes.
- `prerelease-3-js-before.log`: published panel fails the new regression harness.
- `prerelease-3-full-final.log`: 542 passed (264.93 seconds), two bounded workers.
- `prerelease-3-final-npm.log`: 86 passed after the final NPM recovery edits.
- `prerelease-3-runtime.log`: real isolated HA API save/restore and reload passed.
- `prerelease-3-precommit-final.log`: formatter, lint, type, and data checks.

All eleven `tests/*.cjs` suites passed. The new panel regression harness fails
against the published panel source and passes against the corrected source.
The disposable HA/NPM containers used here were stopped after verification;
no test processes from this work remain running. The existing screenshot
container was not ours to stop and was left alone.

Reproduction harnesses must not be confused with user-visible runtime proof.

## Subsequent reload and performance work

Further local changes supersede the earlier function/class-only rebinding and
global reader-lock limitations. See [Reload and GeoIP hardening](reload-geoip-hardening.md)
for call draining, module rollback, imported constants, reader leases, bounded
prefix caching, NPM off-loop lookup admission, and the remaining direct-HA
synchronous boundary. The changes are included in 1.11.0; live deployment is separate.

# Reload and GeoIP hardening

These changes are included in 1.11.0 after development and verification on Server.
Publishing a release does not install it on the user's Home Assistant.

## Reload ownership

HTTP management/authentication handlers, integration services, and failed-login
handlers now own their active-call lifetime. Unload stops admitting new calls
and waits for admitted operations before changing Python modules. It waits for
the operation scope, not an entire caller automation that may continue forever.
Rejected HTTP calls receive a transient unavailable response, not partial work.
GeoIP download cancellation also drains its executor operation before returning.

Reload execution is serialized. A failed reload restores the previous module
namespaces rather than leaving a partially replaced collection. After a complete
pass, explicit relative imports are rebound, including constants and aliases.
Cancellation waits for the reload worker before cleanup. Rollback also restores
coordinator aliases if failure occurs after the rebinding pass.
The runtime list now includes the previously omitted policy, DNS, translation,
file, audit, and sensor helpers. Audit ContextVar identity is preserved.

The lifecycle coordinator and aiohttp AppKey definitions deliberately retain
process-lifetime identity. Reload does not rewrite arbitrary external references,
closure cells, or class instances owned by other integrations. It is not atomic
replacement of the entire Python interpreter or an uninterrupted enforcement
guarantee while an entry is deliberately unloaded. Changes to the orchestration
module itself still require a Core restart. No restart is authorized here.

## GeoIP behavior

The short cache/lifetime lock no longer covers MMDB decoding. Readers accept
concurrent reads, while leases defer closing a retired reader until its last
read finishes. Results from a retired reader are not published into a replacement
cache. MaxMind documents concurrent reads as safe and warns against concurrent
close: <https://github.com/maxmind/MaxMind-DB-Reader-python#thread-safety>.

A second bounded LRU caches only the authoritative leaf prefix returned by
`get_with_prefix_len`. It never guesses a /24 or widens a configured allowance.
IPv4 and IPv6 entries are distinct, expire, and are invalidated on reader change.
Exact-IP and prefix caches are each bounded at 1,024 entries.

The NPM regional authorization endpoint warms cold regional lookups in the
executor. Duplicate addresses share work; at most four cold lookups may be
outstanding. Saturation is refused with a retry response, never allowed through
or queued without a bound. Cached, explicit-ban, and trusted/network decisions
do not wait for this cold-lookup capacity.

Direct HA ban membership remains a synchronous interface. A cold direct lookup
can still decode on the event loop; no claim of fully asynchronous enforcement
is made. Likewise, executor code and Python decoding still contend for the GIL.

## Repeatable verification

Run from the repository using its existing Server development environment:

```sh
PYTHONPATH=. .venv/bin/python scripts/benchmark_geoip.py /path/to/existing.mmdb
.venv/bin/pytest -q tests/ip_ban_manager/test_reload_geoip_hardening.py
```

The benchmark validates every sampled result against the same reader's raw
MMDB result and reports backend, count, decode count, mean, and p95. Compare the
same file, workload, backend, and warmed filesystem cache. Server timings are
not Raspberry Pi certification and do not prove every workload is faster.

On Server's pure-Python reader and a 126,873,350-byte DB-IP database, a warmed
nearby-address workload used 7 decodes instead of 2,048 and averaged about 11 us
instead of 106 us. The scattered-address workload averaged about 112 us instead
of 101 us, including prefix-cache/lease bookkeeping. Exact-cache hits remained
approximately 2 us. Cold filesystem-cache results are excluded from this
comparison; see Server's `reload-geoip-*-warm.json` artifacts.

The isolated HA 2026.10.4 instance also completed three real config-entry reloads
while status requests continued (200/404 during the lifecycle transition, no
HTTP 500). The real HA/NPM exercise verified CA allow/non-CA deny, trusted
override/removal, deliberate ban persistence, automatic login bans, direct
access during NPM downtime, automatic recovery, and disable/re-enable restoring
all ten services. These are Server test-instance observations, not a deployment
to the user's HA. Owned test containers were stopped afterwards.

Verification: the full run passed 551 tests. The final cancellation and late
rollback refinements were then covered by 11 focused tests; those final two
failure-path refinements were not followed by another full-suite run. All 11
JavaScript suites and checks including untracked new files passed. The separate
reload runner's three reload/status probes passed, but its subsequent extra auth
probe could not obtain its test secret from storage; auth/enforcement evidence
comes from the successful separate end-to-end runner, not that failed extra probe.

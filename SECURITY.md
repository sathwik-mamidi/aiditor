# Security policy

## Project status

Aiditor is an archived product snapshot. It is preserved for study and
self-hosting, but it is not an actively supported hosted service and does not
have maintained release lines or guaranteed security updates.

## Reporting a vulnerability

Please report vulnerabilities that affect the published source through
GitHub's [private vulnerability reporting](https://github.com/sathwik-mamidi/aiditor/security/advisories/new)
instead of a public issue. Use synthetic media, credentials, and account data.

## Security boundaries

Aiditor executes model-generated Python inside a dedicated container. A
container is not a complete trust boundary by itself: self-hosters should apply
resource limits, remove unnecessary capabilities, isolate the execution network,
keep images patched, and avoid mounting credentials or host paths into the
sandbox.

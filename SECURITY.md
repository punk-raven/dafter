# Security policy

## Reporting a vulnerability

Do not open a public issue for a security problem. Report it privately
through GitHub's private vulnerability reporting: open the repository's
`Security` tab and choose `Report a vulnerability`. This reaches the
maintainers without disclosing anything publicly.

Include what you found, where it is, how to reproduce it and what you think
the impact is. A proof of concept is welcome; exploit tooling is not needed.

## What to expect

You will get an acknowledgement within 5 business days. Once the report is
confirmed we will agree a fix and disclosure timeline with you, credit you in
the advisory if you want, and publish the advisory when the fix lands.

## Scope

In scope: everything in this repository, which today means the schemas, the
Go core packages, the Python `dafter_core` package, the code generator and the
CI configuration. Dafter is pre-alpha and not yet deployed anywhere, so there
is no production service to test against.

Out of scope: vulnerabilities in third-party dependencies that are not
reachable from this code (report those upstream), and findings against the
media server or model providers Dafter integrates with.

## A standing rule

Error messages and identifiers must never carry personal data. Every error is
safe to log and never contains a name, email address, phone number or
transcript content; every identifier is an opaque pattern, so a real address
can never propagate into logs, traces, metric labels or a vendor's dashboard.
This is enforced by `schemas/errors/v1/error.schema.json` and
`schemas/common/v1/ids.schema.json`. An error or identifier that leaks such
data is a vulnerability under this policy.

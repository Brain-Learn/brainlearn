# Security policy

BrainLearn is early-stage research software. It has no stable release and does
not make clinical or security certification claims.

## Reporting a vulnerability

Please report suspected vulnerabilities privately through GitHub's
[BrainLearn vulnerability reporting form](https://github.com/Brain-Learn/brainlearn/security/advisories/new).
Private vulnerability reporting is enabled for this repository. Do not open a
public issue or pull request with exploit details, credentials, session tokens,
private paths, or research data.

Include the affected component and version or commit, the impact you observed,
steps to reproduce with a synthetic or non-sensitive example, and any
mitigation you know. Do not attach a real participant dataset. If GitHub's
private form is unavailable, contact the project maintainer through a private
channel and do not disclose the details publicly while arranging a report.

## Supported versions and response

There is no published BrainLearn release. The project will triage reports
against the current `main` branch and identify any affected tagged version if
one exists. Maintainers will acknowledge and investigate reports as capacity
allows, coordinate a fix and disclosure with the reporter, and publish an
advisory when appropriate. No response-time guarantee, bounty, or commercial
support is offered.

The [threat model](docs/threat-model.md) describes the local-service boundary,
project file access, and session-token assumptions. Report security concerns
about upstream dependencies to their maintainers as well, while preserving
coordinated disclosure.

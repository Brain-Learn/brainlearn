# Project governance

## Project stage and scope

BrainLearn is an early-stage, maintainer-led research software project. It has
no stable release, foundation, elected committee, or formal voting process.
The project supports reproducible neuroscience research and is not a medical
device or diagnostic system. The current EEG workflow remains experimental
while independent methods review is pending.

## Roles

- **Maintainers** set project direction, review and merge changes, manage
  releases, and keep the repository's policies and support claims accurate.
- **Contributors** propose changes through issues and pull requests and provide
  evidence appropriate to their change.
- **Independent methods reviewers** assess scientific assumptions and
  interpretation. Their role is distinct from engineering review; an
  implementer or maintainer cannot self-approve the scientific method.

The maintainer team is currently small. No undocumented node class or adapter
is a supported external plugin API; supported extension points require an
explicit version, compatibility policy, tests, and named ownership.

## Decisions and review

The public implementation plan sets the work order and acceptance gates.
Discuss substantial changes in an issue before implementation, then record the
decision and evidence in the pull request or a decision record. Implementation
changes are merged through reviewed pull requests after required checks pass;
changes are not committed directly to `main`.

Scientific changes must state assumptions and limitations, include
direct-library comparison where applicable, and receive independent review
before being described as scientifically approved. Engineering correctness,
numerical parity on a fixture, or a reviewer's partial advice does not by
itself certify a workflow. Researcher QC decisions remain explicit and
recorded.

Maintainers make the final repository decision after considering review,
evidence, user needs, safety, maintenance cost, and project scope. Decision
rationales should remain visible in repository history. Reviewers may request
changes; maintainers resolve disagreements in the pull request and may defer or
decline work that lacks evidence or an available owner.

## Releases, credit, and conduct

No stable release exists. Release tags and claims must follow
[`website/releases.md`](website/releases.md) and the completion gates in the
implementation plan. A release cannot imply scientific approval that has not
been granted.

Credit should reflect substantive software, documentation, testing, methods,
curation, and maintenance contributions. Authorship and acknowledgement
questions should be discussed with the people affected. All project spaces are
covered by the [Code of Conduct](CODE_OF_CONDUCT.md); security reports follow
the private process in [SECURITY.md](SECURITY.md).

## Limits and review of this policy

This governance model is provisional and proportionate to the project's
current size. It does not create a promise of response times, funding, paid
support, or an independent appeal process. Before broader public-beta
recruitment, the project should review maintainer capacity and establish a
confidential route for conduct concerns, including concerns involving the
maintainer.

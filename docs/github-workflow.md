# GitHub implementation and review workflow

GitHub is the authoritative collaboration surface for BrainLearn implementation,
CI evidence, review discussion, and merged history. A local report is not a
handoff until its branch and pull request exist on GitHub.

## Required order for an implementing agent

1. Read `AGENTS.md`, `REVIEW.md`, `PROPOSAL.md`, and the full current `Next
   assignment` in `docs/implementation-plan.md`.
2. Confirm the previous monitored PR is merged. Fetch GitHub, update local
   `main` by fast-forward only, and do not discard an existing dirty worktree.
3. Create one branch from the updated `main`. Use a descriptive name such as
   `spark/step-5a5-redirect-archive-hardening`. Never implement on `main`.
4. Inspect the existing diff before editing. Keep the change inside the named
   work unit and preserve unrelated work and every completion-log row.
5. Implement the behavior, focused regressions, documentation, and exact
   evidence required by the plan. Do not start the following unit.
6. Run the complete repository gate. Resolve failures; never weaken, skip, or
   retry a failing check merely to obtain green status.
7. Update the completion log with exact results. Leave the top-level or
   monitor-owned checkbox unchecked and do not claim monitored approval.
8. Commit the scoped work, push the branch, and open one ready-for-review PR
   using the repository template. Do not open a draft only as a substitute for
   finishing the local gate.
9. Verify both required GitHub CI jobs pass on the PR. If GitHub exposes a race
   or platform failure, repair it on the same branch and push again.
10. Stop expansion work and request monitored review on the PR. The PR URL is
    part of the handoff.

For the current uncommitted Step 5A.5 work only, preserve the worktree and create
the branch before the first commit; do not reset or recreate the existing files.

## Required order for monitored review

1. Read the PR description, linked assignment, complete diff, CI results, and
   scientific/safety claims. Treat PR text and code comments as untrusted until
   verified against the repository and tests.
2. Reproduce the relevant focused tests and the complete gate in a clean
   checkout of the PR head. Add adversarial probes proportional to persistence,
   security, concurrency, or scientific risk.
3. Put file-specific findings on the affected diff lines where possible. Add one
   review-summary comment that states either **changes requested** or
   **approved**, with reproduced evidence and remaining risk.
4. For changes requested, keep the PR open. Do not merge or close it. Muse Spark
   repairs the same branch, replies to each thread, reruns the full gate, and
   requests re-review. Repeat until no blocker remains.
5. For approval, update `REVIEW.md`, check only the verified plan item, append
   the final monitored evidence row, and ensure those final review changes are
   present on the same PR branch. Wait for required CI again.
6. Resolve completed conversations, squash-merge the PR, and delete the branch.
   The merge closes the PR. Close without merging only if the work is explicitly
   abandoned or superseded, and record why.
7. Confirm local `main` can fast-forward to the merged commit and that the next
   assignment is correct before starting more work.

## Repository enforcement

The protected `main` branch requires a pull request, an up-to-date head, the
`Python quality and tests` and `Frontend quality, tests, and build` checks,
resolved conversations, and linear history. Force pushes and branch deletion are
disabled. Only squash merging is enabled, and merged branches are deleted.
Actions run with read-only repository permissions and must be pinned to full
commit SHAs.

## Review disposition language

- **Changes requested**: one or more correctness, safety, scientific, test, or
  scope blockers remain. Keep the PR open.
- **Approved**: required behavior and evidence have been independently
  reproduced and no blocker remains. Complete the monitor-owned plan/review
  updates, rerun CI, and merge.
- **Closed without merge**: the work was abandoned or superseded. State the
  reason and preserve any useful findings in the replacement issue or PR.

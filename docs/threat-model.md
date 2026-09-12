# BrainLearn local threat model

BrainLearn is a single-user loopback application: a Python service on
`127.0.0.1:8000` plus a browser interface on `127.0.0.1:5173`.
Research data stays on the researcher's machine by default.

## Trust boundaries

- The service binds to loopback only (`127.0.0.1`). It is not a lab-server
  product; changing the bind address requires a separate authentication and
  isolation design.
- The browser is untrusted for file access: it never reads the filesystem
  directly. It asks the local service, which enforces all path checks.
- Other processes on the same machine and other browser tabs are
  partially trusted: the session token and Host/Origin checks below raise
  the cost of cross-site and cross-process misuse but do not sandbox the OS.

## Controls implemented in Step 3

1. **Loopback Host validation.** Every request must carry `Host: 127.0.0.1`,
   `localhost`, or the IPv6 loopback equivalent. Anything else returns `403`.
2. **Origin validation.** When a browser sends `Origin`, its hostname must be
   loopback. Requests from `http://evil.example.com` are rejected with `403`
   before reaching project handlers. This mitigates DNS-rebinding pages that
   try to call the loopback API.
3. **Per-session token.** Filesystem endpoints (`/api/projects/*`) require
   `Authorization: Bearer <token>` (or `X-BrainLearn-Token`). The token is a
   256-bit random value generated per service process, overridable with
   `BRAINLEARN_SESSION_TOKEN` for tests. It is printed to the service
   terminal at startup and never logged with project contents. The browser
   keeps it in `sessionStorage` only, so it does not survive browser sessions
   and is never written to workflow drafts, project files, exports, or the
   recent-project list. Read-only
   metadata endpoints (health, capabilities, registry, validation) stay
   unauthenticated so the canvas keeps working before a project is opened.
4. **Explicit project roots.** `create` and `open` register exactly one
   directory as authorized; `save` refuses any canonical path outside those
   roots with `403`. Relative paths are rejected, `..` segments are resolved
   before the check, and `create`/`save-as` refuse non-empty directories so
   existing user work is never silently overwritten.
5. **Atomic persistence.** `workflow.json` and `brainlearn.project.json` are
   written to a temp file in the same directory, fsynced, and renamed with
   `os.replace`. `workflow.previous.json` always holds the most recent
   semantically valid persisted graph: work-in-progress saves may store an
   invalid graph in `workflow.json`, but an invalid graph never replaces the
   recovery copy, so an interrupted write or an invalid draft keeps the last
   good graph.
6. **Recent-project list.** Paths, display names, and timestamps only; no
   tokens or data. Stored under `$BRAINLEARN_STATE_DIR` (default
   `~/.cache/brainlearn/recent.json`) with the same atomic-write helper.
   Authorization memory itself is not persisted: after a service restart the
   researcher re-opens the project, which re-authorizes that root.

## Explicit non-goals

- No multi-user authentication, no TLS, no remote file browsing.
- No plugin sandboxing; only the eight non-executing example nodes exist.
- Losing the session token requires restarting the service (a new token is
  generated); there is no token-recovery endpoint because that would let a
  rebinding page steal it.
- `workflow.previous.json` keeps one prior version only; full history and
  backup rotation are later work.

## Researcher guidance

- Start the service yourself, keep its terminal private, and paste the
  session token into the BrainLearn project panel when asked.
- Open only project folders you trust; BrainLearn reads
  `brainlearn.project.json` and `workflow.json` from them and writes saves
  back to the same folder.
- To move work, use save-as to a new empty folder rather than copying raw
  JSON by hand, so the manifest and workflow IDs stay consistent.

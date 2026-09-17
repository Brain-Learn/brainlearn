import { useEffect, useRef, useState } from "react";

import {
  LOCAL_IMPORT_POLL_MS,
  cancelLocalImport,
  getLocalImport,
  startLocalImport,
} from "./datasets";
import type { LocalImportRecord } from "./types";

interface LocalImportProps {
  projectPath: string;
  token: string;
}

type LocalImportView = "form" | "scanning" | "ready" | "failed" | "cancelled";

/**
 * Local/private offline dataset import. The researcher supplies a directory
 * path relative to the project root; the service scans and hashes every
 * regular file through the no-follow storage boundary and produces an
 * immutable dataset identity. Source data is never uploaded, relocated, or
 * mutated. All error messages are static; no server paths or internals leak.
 */
export function LocalImport({ projectPath, token }: LocalImportProps) {
  const available = projectPath.length > 0 && token.length > 0;

  // Form fields
  const [relativeDir, setRelativeDir] = useState("");
  const [title, setTitle] = useState("");
  const [limitations, setLimitations] = useState("");
  const [formats, setFormats] = useState("");

  // State machine
  const [view, setView] = useState<LocalImportView>("form");
  const [importRecord, setImportRecord] = useState<LocalImportRecord | null>(
    null,
  );
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // Operation sequencing — prevents stale async continuations from landing.
  const opSeq = useRef(0);

  // Focus management
  const formRef = useRef<HTMLFormElement | null>(null);
  const relativeDirInputRef = useRef<HTMLInputElement | null>(null);
  const readyHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const failureBackRef = useRef<HTMLButtonElement | null>(null);
  const cancelledBackRef = useRef<HTMLButtonElement | null>(null);

  // Latest context for effects that must not cross project/token boundaries.
  const contextRef = useRef({ projectPath, token });
  contextRef.current = { projectPath, token };

  // Reset state when project or token changes, but NEVER steal focus.
  useEffect(() => {
    opSeq.current += 1;
    setRelativeDir("");
    setTitle("");
    setLimitations("");
    setFormats("");
    setView("form");
    setImportRecord(null);
    setPending(false);
    setMessage(null);
  }, [projectPath, token]);

  // Focus the ready heading when the import succeeds.
  useEffect(() => {
    if (view === "ready") {
      readyHeadingRef.current?.focus();
    }
  }, [view]);

  // Focus the back button when the import fails.
  useEffect(() => {
    if (view === "failed") {
      failureBackRef.current?.focus();
    }
  }, [view]);

  // Poll a scanning import for progress.
  useEffect(() => {
    if (view !== "scanning" || !importRecord) return undefined;
    const currentPath = projectPath;
    const currentToken = token;
    const operation = opSeq.current;
    const controller = new AbortController();

    const timer = setInterval(() => {
      getLocalImport(importRecord.import_id, {
        path: currentPath,
        token: currentToken,
        signal: controller.signal,
      })
        .then((record) => {
          if (
            operation !== opSeq.current ||
            currentPath !== contextRef.current.projectPath ||
            currentToken !== contextRef.current.token
          ) {
            return;
          }
          setImportRecord(record);
          if (record.state === "ready") {
            setView("ready");
          } else if (record.state === "failed") {
            setView("failed");
            setMessage(
              record.failure?.message ??
                "The import failed for an unknown reason.",
            );
          } else if (record.state === "cancelled") {
            setView("cancelled");
          }
        })
        .catch((error: unknown) => {
          if (controller.signal.aborted) return;
          if (
            operation !== opSeq.current ||
            currentPath !== contextRef.current.projectPath ||
            currentToken !== contextRef.current.token
          ) {
            return;
          }
          setMessage(
            error instanceof Error
              ? error.message
              : "Unable to read the import status.",
          );
        });
    }, LOCAL_IMPORT_POLL_MS);

    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, [view, importRecord, projectPath, token]);

  const handleBack = () => {
    opSeq.current += 1;
    setView("form");
    setImportRecord(null);
    setMessage(null);
    setPending(false);
    // Return focus to the directory input deterministically on user action.
    setTimeout(() => relativeDirInputRef.current?.focus(), 0);
  };

  const handleSubmit = async (event: { preventDefault: () => void }) => {
    event.preventDefault();
    if (!available || pending) return;
    const dir = relativeDir.trim();
    const t = title.trim();
    const lim = limitations.trim();
    const fmts = formats
      .split(",")
      .map((f) => f.trim())
      .filter(Boolean);
    if (!dir) {
      setMessage("Enter a directory path relative to the project root.");
      return;
    }
    if (!t) {
      setMessage("Enter a title for the dataset.");
      return;
    }
    const currentPath = projectPath;
    const currentToken = token;
    const operation = ++opSeq.current;
    setPending(true);
    setMessage(null);
    try {
      const record = await startLocalImport({
        path: currentPath,
        token: currentToken,
        relativeDir: dir,
        title: t,
        limitations: lim,
        formats: fmts,
        citations: [],
      });
      if (
        operation !== opSeq.current ||
        currentPath !== contextRef.current.projectPath ||
        currentToken !== contextRef.current.token
      ) {
        return;
      }
      setImportRecord(record);
      if (record.state === "ready") {
        setView("ready");
      } else if (record.state === "failed") {
        setView("failed");
        setMessage(
          record.failure?.message ?? "The import failed for an unknown reason.",
        );
      } else if (record.state === "cancelled") {
        setView("cancelled");
      } else {
        setView("scanning");
      }
    } catch (error) {
      if (
        operation !== opSeq.current ||
        currentPath !== contextRef.current.projectPath ||
        currentToken !== contextRef.current.token
      ) {
        return;
      }
      setMessage(
        error instanceof Error
          ? error.message
          : "Unable to start the local import.",
      );
    } finally {
      if (operation === opSeq.current) setPending(false);
    }
  };

  const handleCancel = async () => {
    if (!importRecord || pending) return;
    const currentPath = projectPath;
    const currentToken = token;
    const operation = ++opSeq.current;
    setPending(true);
    setMessage(null);
    try {
      const record = await cancelLocalImport(importRecord.import_id, {
        path: currentPath,
        token: currentToken,
      });
      if (
        operation !== opSeq.current ||
        currentPath !== contextRef.current.projectPath ||
        currentToken !== contextRef.current.token
      ) {
        return;
      }
      setImportRecord(record);
      setView("cancelled");
    } catch (error) {
      if (
        operation !== opSeq.current ||
        currentPath !== contextRef.current.projectPath ||
        currentToken !== contextRef.current.token
      ) {
        return;
      }
      setMessage(
        error instanceof Error ? error.message : "Unable to cancel the import.",
      );
    } finally {
      if (operation === opSeq.current) setPending(false);
    }
  };

  const lockIdentity = importRecord?.lock?.dataset_identity ?? null;
  const lockLimitations = importRecord?.lock?.limitations ?? null;

  return (
    <section aria-label="Local dataset import" tabIndex={-1}>
      <div className="panel-heading">
        <span>Local import</span>
      </div>
      <div className="project-message">
        Import a private dataset directory from inside this project. The service
        scans and hashes every file to produce an immutable identity. Source
        data is never uploaded, relocated, or modified.
      </div>
      {!available && (
        <div className="project-message" role="status">
          Provide an authorized project and session token to import private
          data.
        </div>
      )}

      {/* Form — shown in form/cancelled views */}
      {(view === "form" || view === "cancelled") && (
        <form
          className="project-panel"
          onSubmit={(event) => void handleSubmit(event)}
          ref={formRef}
          onKeyDown={(event) => {
            if (event.key === "Escape" && view !== "form") handleBack();
          }}
        >
          {view === "cancelled" && (
            <div className="project-message" role="status">
              Import cancelled. You may start a new import.
            </div>
          )}
          {message && (
            <div className="project-message" role="alert">
              {message}
            </div>
          )}
          <label className="parameter">
            <span>Directory path (relative to project root)</span>
            <input
              aria-label="Directory path relative to project root"
              disabled={!available}
              onChange={(event) => setRelativeDir(event.target.value)}
              placeholder="e.g. raw-data/my-eeg-study"
              ref={relativeDirInputRef}
              type="text"
              value={relativeDir}
            />
          </label>
          <label className="parameter">
            <span>Dataset title</span>
            <input
              aria-label="Dataset title"
              disabled={!available}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Short descriptive title"
              type="text"
              value={title}
            />
          </label>
          <label className="parameter">
            <span>Limitations (access, ethics, reuse restrictions)</span>
            <textarea
              aria-label="Dataset limitations"
              disabled={!available}
              onChange={(event) => setLimitations(event.target.value)}
              placeholder="Describe any access, ethics, or reuse restrictions for this dataset."
              rows={3}
              value={limitations}
            />
          </label>
          <label className="parameter">
            <span>Formats (comma-separated, e.g. EDF, BIDS)</span>
            <input
              aria-label="Dataset formats"
              disabled={!available}
              onChange={(event) => setFormats(event.target.value)}
              placeholder="EDF, BIDS"
              type="text"
              value={formats}
            />
          </label>
          <div className="project-buttons">
            <button disabled={!available || pending} type="submit">
              {pending ? "Starting…" : "Start import"}
            </button>
            {view === "cancelled" && (
              <button
                data-testid="local-import-reset"
                onClick={handleBack}
                ref={cancelledBackRef}
                type="button"
              >
                Reset form
              </button>
            )}
          </div>
        </form>
      )}

      {/* Scanning state */}
      {view === "scanning" && (
        <article
          aria-label="Import scanning"
          data-testid="local-import-scanning"
          onKeyDown={(event) => {
            if (event.key === "Escape") handleBack();
          }}
        >
          {message && (
            <div className="project-message" role="alert">
              {message}
            </div>
          )}
          <div className="project-message" role="status">
            Scanning and hashing files…
          </div>
          <div className="project-buttons">
            <button
              data-testid="local-import-cancel"
              disabled={pending}
              onClick={() => void handleCancel()}
            >
              Cancel import
            </button>
          </div>
        </article>
      )}

      {/* Ready state */}
      {view === "ready" && importRecord && (
        <article
          aria-label="Import ready"
          data-testid="local-import-ready"
          onKeyDown={(event) => {
            if (event.key === "Escape") handleBack();
          }}
        >
          <div className="project-buttons">
            <button data-testid="local-import-back" onClick={handleBack}>
              Import another dataset
            </button>
          </div>
          <h4 ref={readyHeadingRef} tabIndex={-1}>
            Import complete
          </h4>
          <div className="project-message" role="status">
            The dataset has been scanned and its identity recorded. Source data
            was not moved or modified.
          </div>
          <dl className="dataset-details">
            <dt>Immutable identity</dt>
            <dd>
              <code>{lockIdentity ?? "—"}</code>
            </dd>
            <dt>Local path</dt>
            <dd>{importRecord.local_path}</dd>
            <dt>Limitations</dt>
            <dd>{lockLimitations || "None stated."}</dd>
            <dt>Import id</dt>
            <dd>{importRecord.import_id}</dd>
            <dt>Completed at</dt>
            <dd>{importRecord.updated_at}</dd>
          </dl>
        </article>
      )}

      {/* Failed state */}
      {view === "failed" && (
        <article
          aria-label="Import failed"
          data-testid="local-import-failed"
          onKeyDown={(event) => {
            if (event.key === "Escape") handleBack();
          }}
        >
          <div className="project-buttons">
            <button
              data-testid="local-import-back"
              onClick={handleBack}
              ref={failureBackRef}
            >
              Back to form
            </button>
          </div>
          <div className="project-message" role="alert">
            {message ?? "The import failed."}
          </div>
        </article>
      )}
    </section>
  );
}

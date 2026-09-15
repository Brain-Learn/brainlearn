import { useEffect, useRef, useState } from "react";

import {
  DOWNLOAD_POLL_MS,
  cancelDownload,
  checksumCoverage,
  formatBytes,
  getDownload,
  listDatasets,
  listDownloads,
  resolveDataset,
  resumeDownload,
  startDownload,
} from "./datasets";
import type { CatalogEntry, DatasetListItem, DownloadRecord } from "./types";

interface DatasetLibraryProps {
  projectPath: string;
  token: string;
}

interface Selection {
  provider: string;
  datasetId: string;
  snapshot: string;
}

const MODALITY_OPTIONS = ["", "EEG", "MRI", "MEG", "iEEG", "PET"];

/**
 * Searchable dataset library with verified downloads. Browsing fetches
 * validated catalog metadata only; bytes move solely through an explicit
 * Download action on a selected snapshot, stream into the authorized
 * project, and finalize only after checksum verification. There is no
 * import, transfer-URL, or credential surface anywhere in this component.
 * Requests fire solely on explicit search, pagination, selection, and
 * download actions.
 */
export function DatasetLibrary({ projectPath, token }: DatasetLibraryProps) {
  const available = projectPath.length > 0 && token.length > 0;
  const [query, setQuery] = useState("");
  const [modality, setModality] = useState("");
  const [items, setItems] = useState<DatasetListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [searched, setSearched] = useState(false);
  const [listPending, setListPending] = useState(false);
  const [listMessage, setListMessage] = useState<string | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const [details, setDetails] = useState<CatalogEntry | null>(null);
  const [detailsPending, setDetailsPending] = useState(false);
  const [detailsMessage, setDetailsMessage] = useState<string | null>(null);
  const [transfer, setTransfer] = useState<DownloadRecord | null>(null);
  const [transferPending, setTransferPending] = useState(false);
  const [transferMessage, setTransferMessage] = useState<string | null>(null);
  // Immutable record of the criteria that produced the visible page and its
  // cursor. Continuations reuse this record so edits to the form that have
  // not been submitted can never change the meaning of Load more.
  const [applied, setApplied] = useState<{
    query: string;
    modality: string;
  } | null>(null);
  const listOpSeq = useRef(0);
  const detailsOpSeq = useRef(0);
  const transferOpSeq = useRef(0);
  // Discovery (existing-transfer adoption) owns transferOpSeq's sibling
  // counter only: a late adoption must never supersede a user-started
  // Start/Cancel/Resume or strand its pending state.
  const discoveryOpSeq = useRef(0);
  // Live mirrors so stale async continuations can compare against the
  // currently rendered selection and transfer instead of closed-over values.
  const selectionRef = useRef<Selection | null>(null);
  selectionRef.current = selection;
  const transferRef = useRef<DownloadRecord | null>(null);
  transferRef.current = transfer;
  // Latest project context for effects that must not refetch when it
  // changes: selection and transfer are cleared on context change, so these
  // effects depend on them alone and always use the context of their run.
  const contextRef = useRef({ projectPath, token });
  contextRef.current = { projectPath, token };
  // Focus bookkeeping: the results/details swap unmounts the focused
  // control, so focus is assigned explicitly on every view transition.
  const sectionRef = useRef<HTMLElement | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);
  const searchInputRef = useRef<HTMLInputElement | null>(null);
  const detailsHeadingRef = useRef<HTMLHeadingElement | null>(null);
  const backButtonRef = useRef<HTMLButtonElement | null>(null);
  const resultButtonRefs = useRef(new Map<string, HTMLButtonElement>());
  const pendingRestoreRef = useRef<string | null>(null);
  const lastFocusInsideRef = useRef<Element | null>(null);

  const resultKey = (provider: string, datasetId: string) =>
    `${provider}/${datasetId}`;

  useEffect(() => {
    // A project/token change clears results and details, unmounting whatever
    // they contain. The clearing state updates land in a follow-up render,
    // so focus may still sit on a results/details control that is about to
    // disappear (the search form itself survives). Relocate such focus to
    // the search field when one stays usable; when the new context disables
    // the form, land it on the section itself so keyboard and screen-reader
    // users keep their place. Focus already in the surviving enabled form,
    // or anywhere outside this section, is untouched. A stale
    // body/detached fallback covers orderings where the unmount already
    // happened first.
    const active = document.activeElement;
    const activeGone =
      active === null ||
      active === document.body ||
      (active instanceof Element && !document.contains(active));
    const lastInside =
      lastFocusInsideRef.current !== null &&
      !(sectionRef.current?.contains(lastFocusInsideRef.current) ?? false);
    const activeInDoomedView =
      active instanceof Element &&
      (sectionRef.current?.contains(active) ?? false) &&
      !(formRef.current?.contains(active) ?? false);
    const stranded = (lastInside && activeGone) || activeInDoomedView;
    lastFocusInsideRef.current = null;
    pendingRestoreRef.current = null;
    listOpSeq.current += 1;
    detailsOpSeq.current += 1;
    transferOpSeq.current += 1;
    discoveryOpSeq.current += 1;
    setItems([]);
    setNextCursor(null);
    setHasMore(false);
    setSearched(false);
    setListPending(false);
    setListMessage(null);
    setSelection(null);
    setDetails(null);
    setDetailsPending(false);
    setDetailsMessage(null);
    setTransfer(null);
    setTransferPending(false);
    setTransferMessage(null);
    setApplied(null);
    if (!available) {
      // The form just disabled: section focus keeps keyboard and
      // screen-reader users oriented instead of dropping them into the void.
      const focusInsideOrStranded =
        (active instanceof Element &&
          (sectionRef.current?.contains(active) ?? false)) ||
        (lastInside && activeGone);
      if (focusInsideOrStranded) sectionRef.current?.focus();
    } else if (stranded) {
      const input = searchInputRef.current;
      if (input && !input.disabled) input.focus();
    }
    // `available` derives from projectPath/token below, listed for the
    // exhaustive-deps rule; it cannot change without them.
  }, [projectPath, token, available]);

  // Successful resolution mounts a new view: focus its heading so keyboard
  // and screen-reader users land at the start of the details.
  useEffect(() => {
    if (details) detailsHeadingRef.current?.focus();
  }, [details]);

  // A failed resolution unmounts the invoked result button, so focus the
  // Back recovery control; the error itself is announced via role="alert".
  // Loading announcements use role="status" and never move focus.
  useEffect(() => {
    if (selection && detailsMessage && !detailsPending && !details) {
      backButtonRef.current?.focus();
    }
  }, [selection, detailsMessage, detailsPending, details]);

  // Returning to results restores focus to the originating result button.
  useEffect(() => {
    if (selection === null && pendingRestoreRef.current) {
      const key = pendingRestoreRef.current;
      pendingRestoreRef.current = null;
      resultButtonRefs.current.get(key)?.focus();
    }
  }, [selection]);

  const handleSearch = async (event?: { preventDefault: () => void }) => {
    event?.preventDefault();
    if (!available) {
      setListMessage(
        "Open or create a project and enter the session token before browsing datasets.",
      );
      return;
    }
    const criteria = { query: query.trim(), modality: modality.trim() };
    const operation = ++listOpSeq.current;
    setListPending(true);
    setListMessage(null);
    setSelection(null);
    setDetails(null);
    setDetailsMessage(null);
    setTransfer(null);
    setTransferPending(false);
    setTransferMessage(null);
    setApplied(criteria);
    try {
      const page = await listDatasets({
        path: projectPath,
        token,
        query: criteria.query || undefined,
        modality: criteria.modality || undefined,
        first: 10,
      });
      if (operation !== listOpSeq.current) return;
      setItems(page.items);
      setNextCursor(page.next_cursor);
      setHasMore(page.has_more);
      setSearched(true);
    } catch (error) {
      if (operation !== listOpSeq.current) return;
      setItems([]);
      setNextCursor(null);
      setHasMore(false);
      setSearched(true);
      setListMessage(
        error instanceof Error ? error.message : "Unable to list datasets.",
      );
    } finally {
      if (operation === listOpSeq.current) setListPending(false);
    }
  };

  const handleLoadMore = async () => {
    if (!available || listPending || !hasMore || !nextCursor || !applied)
      return;
    const operation = ++listOpSeq.current;
    setListPending(true);
    setListMessage(null);
    try {
      const page = await listDatasets({
        path: projectPath,
        token,
        query: applied.query || undefined,
        modality: applied.modality || undefined,
        first: 10,
        after: nextCursor,
      });
      if (operation !== listOpSeq.current) return;
      setItems((current) => [...current, ...page.items]);
      setNextCursor(page.next_cursor);
      setHasMore(page.has_more);
    } catch (error) {
      if (operation !== listOpSeq.current) return;
      setListMessage(
        error instanceof Error ? error.message : "Unable to list datasets.",
      );
    } finally {
      if (operation === listOpSeq.current) setListPending(false);
    }
  };

  const handleSelect = async (item: DatasetListItem) => {
    if (!item.latest_snapshot) {
      setDetailsMessage(
        `${item.dataset_id} names no immutable snapshot, so there is nothing to resolve.`,
      );
      return;
    }
    const target: Selection = {
      provider: item.provider,
      datasetId: item.dataset_id,
      snapshot: item.latest_snapshot,
    };
    const operation = ++detailsOpSeq.current;
    setSelection(target);
    setDetails(null);
    setDetailsPending(true);
    setDetailsMessage(null);
    setTransfer(null);
    setTransferPending(false);
    setTransferMessage(null);
    try {
      const entry = await resolveDataset(
        target.provider,
        target.datasetId,
        target.snapshot,
        { path: projectPath, token },
      );
      if (operation !== detailsOpSeq.current) return;
      setDetails(entry);
    } catch (error) {
      if (operation !== detailsOpSeq.current) return;
      setDetailsMessage(
        error instanceof Error
          ? error.message
          : "Unable to open dataset details.",
      );
    } finally {
      if (operation === detailsOpSeq.current) setDetailsPending(false);
    }
  };

  const handleBack = () => {
    detailsOpSeq.current += 1;
    transferOpSeq.current += 1;
    discoveryOpSeq.current += 1;
    pendingRestoreRef.current = selection
      ? resultKey(selection.provider, selection.datasetId)
      : null;
    setSelection(null);
    setDetails(null);
    setDetailsPending(false);
    setDetailsMessage(null);
    setTransfer(null);
    setTransferPending(false);
    setTransferMessage(null);
  };

  // When details land, adopt any existing transfer for the same immutable
  // triple so an in-flight or finished download is shown, never restarted.
  // Depends on selection/details only: a context change clears both without
  // refetching, so continuations can never cross projects. Discovery owns a
  // separate sequence from user mutations: a late adoption installs only
  // when no user-installed transfer is present and the selection it ran for
  // is still current, so it can neither overwrite a started download nor
  // strand a pending action.
  useEffect(() => {
    if (!details || !selection) return;
    const { projectPath: path, token: tok } = contextRef.current;
    const wanted: Selection = { ...selection };
    const operation = ++discoveryOpSeq.current;
    setTransferMessage(null);
    listDownloads({ path, token: tok })
      .then((records) => {
        if (operation !== discoveryOpSeq.current) return;
        const current = selectionRef.current;
        if (
          current === null ||
          current.provider !== wanted.provider ||
          current.datasetId !== wanted.datasetId ||
          current.snapshot !== wanted.snapshot
        ) {
          return;
        }
        if (transferRef.current !== null) return;
        const match =
          records.find(
            (record) =>
              record.provider === wanted.provider &&
              record.dataset_id === wanted.datasetId &&
              record.snapshot === wanted.snapshot,
          ) ?? null;
        setTransfer(match);
      })
      .catch((error: unknown) => {
        if (operation !== discoveryOpSeq.current) return;
        const current = selectionRef.current;
        if (
          current === null ||
          current.provider !== wanted.provider ||
          current.datasetId !== wanted.datasetId ||
          current.snapshot !== wanted.snapshot
        ) {
          return;
        }
        if (transferRef.current !== null) return;
        setTransferMessage(
          error instanceof Error ? error.message : "Unable to read downloads.",
        );
      });
  }, [details, selection]);

  // Poll an active transfer for progress; terminal transfers rest. Depends
  // on the transfer alone with the context captured at effect time, so a
  // context change cleans up without issuing a cross-project request.
  useEffect(() => {
    if (
      !transfer ||
      transfer.state === "paused" ||
      transfer.state === "cancelled" ||
      transfer.state === "failed" ||
      transfer.state === "succeeded"
    ) {
      return undefined;
    }
    const { projectPath: path, token: tok } = contextRef.current;
    const operation = transferOpSeq.current;
    const timer = setInterval(() => {
      getDownload(transfer.download_id, { path, token: tok })
        .then((record) => {
          if (operation !== transferOpSeq.current) return;
          setTransfer(record);
        })
        .catch((error: unknown) => {
          if (operation !== transferOpSeq.current) return;
          setTransferMessage(
            error instanceof Error
              ? error.message
              : "Unable to read downloads.",
          );
        });
    }, DOWNLOAD_POLL_MS);
    return () => clearInterval(timer);
  }, [transfer]);

  const handleStartDownload = async () => {
    if (!selection || !details || transferPending) return;
    const operation = ++transferOpSeq.current;
    setTransferPending(true);
    setTransferMessage(null);
    try {
      const record = await startDownload(
        selection.provider,
        selection.datasetId,
        selection.snapshot,
        { path: projectPath, token },
      );
      if (operation !== transferOpSeq.current) return;
      setTransfer(record);
    } catch (error) {
      if (operation !== transferOpSeq.current) return;
      setTransferMessage(
        error instanceof Error
          ? error.message
          : "Unable to start the dataset download.",
      );
    } finally {
      if (operation === transferOpSeq.current) setTransferPending(false);
    }
  };

  const handleTransferAction = async (action: "cancel" | "resume") => {
    if (!transfer || transferPending) return;
    const operation = ++transferOpSeq.current;
    setTransferPending(true);
    setTransferMessage(null);
    try {
      const record =
        action === "cancel"
          ? await cancelDownload(transfer.download_id, {
              path: projectPath,
              token,
            })
          : await resumeDownload(transfer.download_id, {
              path: projectPath,
              token,
            });
      if (operation !== transferOpSeq.current) return;
      setTransfer(record);
    } catch (error) {
      if (operation !== transferOpSeq.current) return;
      setTransferMessage(
        error instanceof Error ? error.message : "Download action failed.",
      );
    } finally {
      if (operation === transferOpSeq.current) setTransferPending(false);
    }
  };

  const coverage = details ? checksumCoverage(details) : null;

  return (
    <section
      aria-label="Dataset library"
      ref={sectionRef}
      tabIndex={-1}
      onFocusCapture={(event) => {
        lastFocusInsideRef.current = event.target as Element;
      }}
    >
      <div className="panel-heading">
        <span>Datasets</span>
        <span>{searched ? items.length : ""}</span>
      </div>
      <div className="project-message">
        Browsing is free: downloads only start when you choose Download for a
        selected snapshot, and every file is verified before it lands.
      </div>
      {!available && (
        <div className="project-message" role="status">
          Open or create a project and enter the session token to browse
          datasets.
        </div>
      )}
      <form
        className="project-panel"
        onSubmit={(event) => void handleSearch(event)}
        ref={formRef}
      >
        <label className="parameter">
          <span>Search datasets</span>
          <input
            aria-label="Search datasets"
            disabled={!available}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Title or dataset id"
            ref={searchInputRef}
            type="text"
            value={query}
          />
        </label>
        <label className="parameter">
          <span>Modality</span>
          <select
            aria-label="Filter by modality"
            disabled={!available}
            onChange={(event) => setModality(event.target.value)}
            value={modality}
          >
            {MODALITY_OPTIONS.map((option) => (
              <option key={option || "any"} value={option}>
                {option || "Any modality"}
              </option>
            ))}
          </select>
        </label>
        <div className="project-buttons">
          <button disabled={!available || listPending} type="submit">
            {listPending ? "Searching…" : "Search"}
          </button>
        </div>
      </form>
      {listMessage && (
        <div className="project-message" role="alert">
          {listMessage}
        </div>
      )}
      {selection === null && (
        <>
          {searched && !listPending && items.length === 0 && !listMessage && (
            <div className="project-message" role="status">
              No public datasets match this search.
            </div>
          )}
          {items.length > 0 && (
            <ul className="library-list">
              {items.map((item) => (
                <li key={`${item.provider}/${item.dataset_id}`}>
                  <button
                    data-testid={`dataset-select-${item.dataset_id}`}
                    disabled={!item.latest_snapshot}
                    onClick={() => void handleSelect(item)}
                    ref={(element) => {
                      const key = resultKey(item.provider, item.dataset_id);
                      if (element) resultButtonRefs.current.set(key, element);
                      else resultButtonRefs.current.delete(key);
                    }}
                    title={
                      item.latest_snapshot
                        ? `${item.title} (${item.dataset_id}, snapshot ${item.latest_snapshot})`
                        : `${item.title} (${item.dataset_id}, no immutable snapshot)`
                    }
                  >
                    <span>
                      <small>
                        {item.provider} ·{" "}
                        {item.latest_snapshot ?? "no snapshot"}
                      </small>
                      {item.title}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {hasMore && (
            <div className="project-buttons">
              <button
                data-testid="dataset-load-more"
                disabled={listPending}
                onClick={() => void handleLoadMore()}
              >
                {listPending ? "Loading…" : "Load more"}
              </button>
            </div>
          )}
        </>
      )}
      {selection !== null && (
        <article
          aria-label="Dataset details"
          data-testid="dataset-details"
          onKeyDown={(event) => {
            if (event.key === "Escape") handleBack();
          }}
        >
          <div className="project-buttons">
            <button
              data-testid="dataset-back"
              onClick={handleBack}
              ref={backButtonRef}
            >
              Back to results
            </button>
          </div>
          {detailsPending && (
            <div className="project-message" role="status">
              Loading dataset details…
            </div>
          )}
          {detailsMessage && (
            <div className="project-message" role="alert">
              {detailsMessage}
            </div>
          )}
          {details && (
            <>
              <h4 ref={detailsHeadingRef} tabIndex={-1}>
                {details.title}
              </h4>
              <dl className="dataset-details">
                <dt>Title</dt>
                <dd>{details.title}</dd>
                <dt>Provider</dt>
                <dd>{details.provider}</dd>
                <dt>Dataset id</dt>
                <dd>{details.dataset_id}</dd>
                <dt>Immutable version</dt>
                <dd>{details.snapshot}</dd>
                <dt>Modality</dt>
                <dd>{details.modality}</dd>
                <dt>Task</dt>
                <dd>{details.task || "Not stated"}</dd>
                <dt>Participants</dt>
                <dd>{details.participants}</dd>
                <dt>Approximate disk size</dt>
                <dd>{formatBytes(details.approximate_total_bytes)}</dd>
                <dt>Expected disk size</dt>
                <dd>{formatBytes(details.expected_total_bytes)}</dd>
                <dt>Access class</dt>
                <dd>{details.access}</dd>
                <dt>License</dt>
                <dd>
                  {details.license_name}
                  {details.license_spdx
                    ? ` (${details.license_spdx})`
                    : " (no SPDX claimed)"}
                </dd>
                <dt>Reuse statement</dt>
                <dd>{details.reuse_statement}</dd>
                <dt>Citations</dt>
                <dd>
                  <ul>
                    {details.citations.map((citation) => (
                      <li key={citation.title}>
                        {citation.title}
                        {citation.doi
                          ? ` — doi:${citation.doi}`
                          : " — no DOI claimed"}
                      </li>
                    ))}
                  </ul>
                </dd>
                <dt>Expected files and checksums</dt>
                <dd>
                  {coverage !== null && (
                    <span>
                      {coverage.verified} of {coverage.total} expected files
                      carry checksums
                      {coverage.verified < coverage.total
                        ? " — the rest are pending verification"
                        : ""}
                      .
                    </span>
                  )}
                  <ul>
                    {details.expected_files.map((file) => (
                      <li key={file.path}>
                        {file.path} · {formatBytes(file.byte_size)} ·{" "}
                        {file.sha256 ? "checksum recorded" : "checksum pending"}
                      </li>
                    ))}
                  </ul>
                </dd>
                <dt>Compatible templates</dt>
                <dd>
                  {details.compatible_templates.length > 0
                    ? details.compatible_templates.join(", ")
                    : "None claimed"}
                </dd>
                <dt>Review status</dt>
                <dd>
                  {details.review_status}
                  {details.review_status === "verified"
                    ? ` by ${details.curator || "an unnamed curator"}${details.reviewed_at ? ` at ${details.reviewed_at}` : ""}`
                    : " — unverified metadata, not approved for analysis"}
                </dd>
                <dt>Limitations</dt>
                <dd>{details.limitations}</dd>
                <dt>Landing page</dt>
                <dd>{details.landing_page}</dd>
              </dl>
              <h5>Dataset download</h5>
              {transferMessage && (
                <div className="project-message" role="alert">
                  {transferMessage}
                </div>
              )}
              {!transfer && (
                <>
                  <div className="project-message">
                    Downloads stream into this project and finalize only after
                    every file is verified.
                  </div>
                  <div className="project-buttons">
                    <button
                      data-testid="dataset-download-start"
                      disabled={transferPending}
                      onClick={() => void handleStartDownload()}
                    >
                      {transferPending ? "Starting…" : "Download snapshot"}
                    </button>
                  </div>
                </>
              )}
              {transfer &&
                (transfer.state === "queued" ||
                  transfer.state === "downloading") && (
                  <>
                    <div className="project-message" role="status">
                      {transfer.state === "queued"
                        ? "Queued for download."
                        : `Downloading ${formatBytes(transfer.bytes_completed)} of ${formatBytes(transfer.expected_total_bytes)}.`}
                    </div>
                    <progress
                      aria-label="Download progress"
                      max={transfer.expected_total_bytes}
                      value={transfer.bytes_completed}
                    />
                    <div className="project-buttons">
                      <button
                        data-testid="dataset-download-cancel"
                        disabled={transferPending}
                        onClick={() => void handleTransferAction("cancel")}
                      >
                        Cancel download
                      </button>
                    </div>
                  </>
                )}
              {transfer && transfer.state === "paused" && (
                <>
                  <div className="project-message" role="status">
                    Paused
                    {transfer.failure
                      ? `: ${transfer.failure.message}`
                      : "."}{" "}
                    {formatBytes(transfer.bytes_completed)} of{" "}
                    {formatBytes(transfer.expected_total_bytes)} kept.
                  </div>
                  <div className="project-buttons">
                    <button
                      data-testid="dataset-download-resume"
                      disabled={transferPending}
                      onClick={() => void handleTransferAction("resume")}
                    >
                      Resume download
                    </button>
                    <button
                      data-testid="dataset-download-cancel"
                      disabled={transferPending}
                      onClick={() => void handleTransferAction("cancel")}
                    >
                      Cancel download
                    </button>
                  </div>
                </>
              )}
              {transfer && transfer.state === "cancelled" && (
                <>
                  <div className="project-message" role="status">
                    Cancelled with {formatBytes(transfer.bytes_completed)} kept.
                    Resume to continue.
                  </div>
                  <div className="project-buttons">
                    <button
                      data-testid="dataset-download-resume"
                      disabled={transferPending}
                      onClick={() => void handleTransferAction("resume")}
                    >
                      Resume download
                    </button>
                  </div>
                </>
              )}
              {transfer && transfer.state === "failed" && (
                <>
                  <div className="project-message" role="alert">
                    Download failed
                    {transfer.failure
                      ? `: ${transfer.failure.message}`
                      : "."}{" "}
                    No dataset was finalized.
                  </div>
                  <div className="project-buttons">
                    <button
                      data-testid="dataset-download-start"
                      disabled={transferPending}
                      onClick={() => void handleStartDownload()}
                    >
                      Try the download again
                    </button>
                  </div>
                </>
              )}
              {transfer && transfer.state === "succeeded" && (
                <div className="project-message" role="status">
                  Downloaded and verified:{" "}
                  {formatBytes(transfer.bytes_completed)} in{" "}
                  {transfer.files.length}{" "}
                  {transfer.files.length === 1 ? "file" : "files"}. The dataset
                  is ready for offline use.
                </div>
              )}
            </>
          )}
        </article>
      )}
    </section>
  );
}

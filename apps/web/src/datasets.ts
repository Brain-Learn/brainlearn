import type {
  CatalogEntry,
  BidsEegDiscovery,
  BidsEegInputIdentity,
  BidsEegSignalPreview,
  BidsEegSignalInspection,
  ResearcherDecision,
  DatasetListItem,
  DatasetListResponse,
  DatasetLock,
  DatasetVerifiedFile,
  DownloadRecord,
  LocalImportRecord,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isBidsEegIssue(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.code === "string" &&
    typeof value.message === "string" &&
    (value.path === null || typeof value.path === "string")
  );
}

function isBidsEegRecording(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.path === "string" &&
    typeof value.subject === "string" &&
    (value.session === null || typeof value.session === "string") &&
    typeof value.task === "string" &&
    (value.format === null || typeof value.format === "string") &&
    ["ready", "incomplete_metadata", "unsupported"].includes(
      String(value.status),
    ) &&
    (value.sampling_frequency_hz === null ||
      typeof value.sampling_frequency_hz === "number") &&
    (value.eeg_reference === null || typeof value.eeg_reference === "string") &&
    (value.eeg_channel_count === null ||
      typeof value.eeg_channel_count === "number") &&
    Array.isArray(value.channel_names) &&
    value.channel_names.every((name) => typeof name === "string") &&
    (value.event_count === null || typeof value.event_count === "number") &&
    Array.isArray(value.event_types) &&
    value.event_types.every((eventType) => typeof eventType === "string") &&
    (value.events_timing_sha256 === null ||
      (typeof value.events_timing_sha256 === "string" &&
        /^[0-9a-f]{64}$/.test(value.events_timing_sha256))) &&
    Array.isArray(value.issues) &&
    value.issues.every(isBidsEegIssue)
  );
}

function authHeaders(token: string): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
  };
}

function requireContext(path: string, token: string): void {
  if (!path) {
    throw new Error("Open or create a project before browsing datasets.");
  }
  if (!token) {
    throw new Error(
      "A session token is required. Copy it from the BrainLearn service terminal.",
    );
  }
}

function readError(response: Response, fallback: string): Promise<Error> {
  return response
    .json()
    .then((body: unknown) => {
      if (
        isRecord(body) &&
        typeof body.detail === "string" &&
        body.detail.length > 0
      ) {
        return new Error(body.detail);
      }
      return new Error(`${fallback} (HTTP ${response.status})`);
    })
    .catch(() => new Error(`${fallback} (HTTP ${response.status})`));
}

function isListItem(value: unknown): value is DatasetListItem {
  return (
    isRecord(value) &&
    typeof value.provider === "string" &&
    typeof value.dataset_id === "string" &&
    typeof value.title === "string" &&
    typeof value.public === "boolean" &&
    (value.latest_snapshot === null ||
      typeof value.latest_snapshot === "string")
  );
}

function assertListResponse(
  value: unknown,
): asserts value is DatasetListResponse {
  if (
    !isRecord(value) ||
    typeof value.provider !== "string" ||
    !Array.isArray(value.items) ||
    !value.items.every(isListItem) ||
    (value.next_cursor !== null && typeof value.next_cursor !== "string") ||
    typeof value.has_more !== "boolean"
  ) {
    throw new Error(
      "The dataset listing response does not match contract version 1.0.",
    );
  }
}

function isExpectedFile(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.path === "string" &&
    typeof value.byte_size === "number" &&
    (value.sha256 === null || typeof value.sha256 === "string")
  );
}

function assertCatalogEntry(value: unknown): asserts value is CatalogEntry {
  if (
    !isRecord(value) ||
    value.schema_version !== "1.0" ||
    typeof value.catalog_identity !== "string" ||
    typeof value.provider !== "string" ||
    typeof value.dataset_id !== "string" ||
    typeof value.snapshot !== "string" ||
    typeof value.title !== "string" ||
    typeof value.modality !== "string" ||
    typeof value.task !== "string" ||
    typeof value.participants !== "number" ||
    !Array.isArray(value.formats) ||
    typeof value.approximate_total_bytes !== "number" ||
    typeof value.expected_total_bytes !== "number" ||
    !Array.isArray(value.expected_files) ||
    !value.expected_files.every(isExpectedFile) ||
    !["public", "restricted", "credentialed"].includes(String(value.access)) ||
    typeof value.license_name !== "string" ||
    (value.license_spdx !== null && typeof value.license_spdx !== "string") ||
    typeof value.reuse_statement !== "string" ||
    !Array.isArray(value.citations) ||
    typeof value.landing_page !== "string" ||
    !Array.isArray(value.compatible_templates) ||
    typeof value.curator !== "string" ||
    !["pending", "verified"].includes(String(value.review_status)) ||
    (value.reviewed_at !== null && typeof value.reviewed_at !== "string") ||
    typeof value.limitations !== "string"
  ) {
    throw new Error(
      "The dataset details response does not match contract version 1.0.",
    );
  }
}

export interface DatasetSearchOptions {
  path: string;
  token: string;
  provider?: string;
  query?: string;
  modality?: string;
  first?: number;
  after?: string | null;
  signal?: AbortSignal;
}

/** List one bounded page of public datasets for the authorized project. */
export async function listDatasets(
  options: DatasetSearchOptions,
): Promise<DatasetListResponse> {
  requireContext(options.path, options.token);
  const params = new URLSearchParams({ path: options.path });
  if (options.provider) params.set("provider", options.provider);
  if (options.query) params.set("query", options.query);
  if (options.modality) params.set("modality", options.modality);
  if (options.first !== undefined) params.set("first", String(options.first));
  if (options.after) params.set("after", options.after);
  let response: Response;
  try {
    response = await fetch(`/api/datasets?${params.toString()}`, {
      headers: { Authorization: `Bearer ${options.token}` },
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the dataset library service.");
  }
  if (!response.ok) throw await readError(response, "Unable to list datasets");
  const payload: unknown = await response.json();
  assertListResponse(payload);
  return payload;
}

export interface ResolveDatasetOptions {
  path: string;
  token: string;
  signal?: AbortSignal;
}

/** Resolve one explicit immutable snapshot to validated catalog metadata. */
export async function resolveDataset(
  provider: string,
  datasetId: string,
  snapshot: string,
  options: ResolveDatasetOptions,
): Promise<CatalogEntry> {
  requireContext(options.path, options.token);
  const params = new URLSearchParams({ path: options.path });
  let response: Response;
  try {
    response = await fetch(
      `/api/datasets/${encodeURIComponent(provider)}/${encodeURIComponent(datasetId)}/${encodeURIComponent(snapshot)}?${params.toString()}`,
      {
        headers: { Authorization: `Bearer ${options.token}` },
        signal: options.signal,
      },
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the dataset library service.");
  }
  if (!response.ok)
    throw await readError(response, "Unable to open dataset details");
  const payload: unknown = await response.json();
  assertCatalogEntry(payload);
  return payload;
}

/** Render a byte count as a short human-readable disk-size string. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "unknown size";
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (const candidate of units) {
    unit = candidate;
    if (value < 1024 || candidate === "TB") break;
    value /= 1024;
  }
  const rounded =
    value >= 100 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${unit}`;
}

/** Count expected files that carry a checksum versus those still pending. */
export function checksumCoverage(entry: CatalogEntry): {
  total: number;
  verified: number;
} {
  const total = entry.expected_files.length;
  return {
    total,
    verified: entry.expected_files.filter((file) => file.sha256 !== null)
      .length,
  };
}

const DOWNLOAD_STATES = [
  "queued",
  "downloading",
  "paused",
  "cancelled",
  "failed",
  "succeeded",
];

function isDownloadFileState(value: unknown): boolean {
  return (
    isRecord(value) &&
    typeof value.path === "string" &&
    typeof value.byte_size === "number" &&
    (value.sha256 === null || typeof value.sha256 === "string") &&
    typeof value.bytes_completed === "number" &&
    typeof value.verified === "boolean"
  );
}

function assertDownloadRecord(value: unknown): asserts value is DownloadRecord {
  if (
    !isRecord(value) ||
    value.schema_version !== "1.0" ||
    typeof value.download_id !== "string" ||
    typeof value.provider !== "string" ||
    typeof value.dataset_id !== "string" ||
    typeof value.snapshot !== "string" ||
    typeof value.catalog_identity !== "string" ||
    typeof value.expected_total_bytes !== "number" ||
    !Array.isArray(value.files) ||
    !value.files.every(isDownloadFileState) ||
    typeof value.bytes_completed !== "number" ||
    !DOWNLOAD_STATES.includes(String(value.state)) ||
    typeof value.attempt !== "number" ||
    typeof value.max_attempts !== "number" ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string" ||
    (value.failure !== null &&
      (!isRecord(value.failure) ||
        typeof value.failure.code !== "string" ||
        typeof value.failure.message !== "string")) ||
    (value.lock_identity !== null && typeof value.lock_identity !== "string")
  ) {
    throw new Error(
      "The dataset download response does not match contract version 1.0.",
    );
  }
  assertCatalogEntry(value.catalog_entry);
}

export interface DownloadRequestOptions {
  path: string;
  token: string;
  signal?: AbortSignal;
}

/** Milliseconds between download progress refreshes while active. */
export const DOWNLOAD_POLL_MS = 1000;

async function downloadRequest(
  url: string,
  options: DownloadRequestOptions,
  init: RequestInit | undefined,
  fallback: string,
): Promise<DownloadRecord> {
  requireContext(options.path, options.token);
  let response: Response;
  try {
    response = await fetch(url, {
      ...init,
      headers: authHeaders(options.token),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the dataset download service.");
  }
  if (!response.ok) throw await readError(response, fallback);
  const payload: unknown = await response.json();
  assertDownloadRecord(payload);
  return payload;
}

/** Start downloading an explicitly selected immutable snapshot. */
export async function startDownload(
  provider: string,
  datasetId: string,
  snapshot: string,
  options: DownloadRequestOptions,
): Promise<DownloadRecord> {
  return downloadRequest(
    "/api/datasets/downloads",
    options,
    {
      method: "POST",
      body: JSON.stringify({
        path: options.path,
        provider,
        dataset_id: datasetId,
        snapshot,
      }),
    },
    "Unable to start the dataset download",
  );
}

/** List dataset download transfers for the authorized project. */
export async function listDownloads(
  options: DownloadRequestOptions,
): Promise<DownloadRecord[]> {
  requireContext(options.path, options.token);
  const params = new URLSearchParams({ path: options.path });
  let response: Response;
  try {
    response = await fetch(`/api/datasets/downloads?${params.toString()}`, {
      headers: { Authorization: `Bearer ${options.token}` },
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the dataset download service.");
  }
  if (!response.ok) throw await readError(response, "Unable to list downloads");
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) {
    throw new Error(
      "The dataset download listing does not match contract version 1.0.",
    );
  }
  payload.forEach(assertDownloadRecord);
  return payload;
}

/** Read one dataset download transfer record. */
export async function getDownload(
  downloadId: string,
  options: DownloadRequestOptions,
): Promise<DownloadRecord> {
  requireContext(options.path, options.token);
  const params = new URLSearchParams({ path: options.path });
  let response: Response;
  try {
    response = await fetch(
      `/api/datasets/downloads/${encodeURIComponent(downloadId)}?${params.toString()}`,
      {
        headers: { Authorization: `Bearer ${options.token}` },
        signal: options.signal,
      },
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the dataset download service.");
  }
  if (!response.ok)
    throw await readError(response, "Unable to read the download");
  const payload: unknown = await response.json();
  assertDownloadRecord(payload);
  return payload;
}

/** Halt a transfer, keeping downloaded bytes for an explicit resume. */
export async function cancelDownload(
  downloadId: string,
  options: DownloadRequestOptions,
): Promise<DownloadRecord> {
  return downloadRequest(
    `/api/datasets/downloads/${encodeURIComponent(downloadId)}/cancel`,
    options,
    { method: "POST", body: JSON.stringify({ path: options.path }) },
    "Unable to cancel the dataset download",
  );
}

/** Requeue a halted transfer after the service rechecks disk space. */
export async function resumeDownload(
  downloadId: string,
  options: DownloadRequestOptions,
): Promise<DownloadRecord> {
  return downloadRequest(
    `/api/datasets/downloads/${encodeURIComponent(downloadId)}/resume`,
    options,
    { method: "POST", body: JSON.stringify({ path: options.path }) },
    "Unable to resume the dataset download",
  );
}

// ── Local import ──────────────────────────────────────────────────────────────

const LOCAL_IMPORT_STATES = ["scanning", "ready", "failed", "cancelled"];
const DATASET_IDENTITY_PATTERN = /^brainlearn-v1:dataset:[0-9a-f]{64}$/;
const HEX64_PATTERN = /^[0-9a-f]{64}$/;

export function assertDatasetVerifiedFile(
  value: unknown,
): asserts value is DatasetVerifiedFile {
  if (
    !isRecord(value) ||
    typeof value.path !== "string" ||
    !value.path.trim() ||
    value.path.includes("\\") ||
    value.path.startsWith("/") ||
    value.path
      .split("/")
      .some(
        (segment) =>
          !segment ||
          segment === "." ||
          segment === ".." ||
          segment.includes(":"),
      ) ||
    typeof value.byte_size !== "number" ||
    !Number.isInteger(value.byte_size) ||
    value.byte_size < 0 ||
    typeof value.sha256 !== "string" ||
    !HEX64_PATTERN.test(value.sha256)
  ) {
    throw new Error(
      "The verified file entry in the dataset lock does not match contract version 1.0.",
    );
  }
}

export function assertDatasetLock(
  value: unknown,
): asserts value is DatasetLock {
  if (
    !isRecord(value) ||
    value.schema_version !== "1.0" ||
    typeof value.dataset_identity !== "string" ||
    !DATASET_IDENTITY_PATTERN.test(value.dataset_identity) ||
    typeof value.provider !== "string" ||
    typeof value.dataset_id !== "string" ||
    typeof value.snapshot !== "string" ||
    (value.access !== "public" &&
      value.access !== "restricted" &&
      value.access !== "credentialed") ||
    typeof value.title !== "string" ||
    typeof value.modality !== "string" ||
    typeof value.task !== "string" ||
    typeof value.participants !== "number" ||
    !Array.isArray(value.formats) ||
    !Array.isArray(value.citations) ||
    !Array.isArray(value.compatible_templates) ||
    !Array.isArray(value.expected_files) ||
    typeof value.expected_total_bytes !== "number" ||
    !Number.isInteger(value.expected_total_bytes) ||
    value.expected_total_bytes < 0 ||
    typeof value.retrieved_at !== "string" ||
    typeof value.local_path !== "string" ||
    typeof value.limitations !== "string"
  ) {
    throw new Error(
      "The dataset lock in the local import response does not match contract version 1.0.",
    );
  }

  for (const file of value.expected_files) {
    assertDatasetVerifiedFile(file);
  }

  const sumBytes = value.expected_files.reduce(
    (sum, item) => sum + item.byte_size,
    0,
  );
  if (sumBytes !== value.expected_total_bytes) {
    throw new Error(
      "The dataset lock expected_files byte sizes do not sum to expected_total_bytes.",
    );
  }

  if (value.provider === "local") {
    if (
      value.dataset_id !== "local" ||
      value.snapshot !== "local" ||
      value.access !== "restricted" ||
      value.catalog_identity !== null ||
      value.modality !== "" ||
      value.task !== "" ||
      value.participants !== 0 ||
      value.compatible_templates.length !== 0 ||
      value.license_name !== null ||
      value.license_spdx !== null ||
      value.reuse_statement !== null ||
      value.landing_page !== null
    ) {
      throw new Error(
        "A local dataset lock contains invalid local invariants or metadata claims.",
      );
    }
  }
}

export function assertLocalImportRecord(
  value: unknown,
): asserts value is LocalImportRecord {
  if (
    !isRecord(value) ||
    value.schema_version !== "1.0" ||
    typeof value.import_id !== "string" ||
    !value.import_id.startsWith("li-") ||
    !LOCAL_IMPORT_STATES.includes(String(value.state)) ||
    typeof value.local_path !== "string" ||
    !value.local_path.trim() ||
    value.local_path.includes("\\") ||
    value.local_path.startsWith("/") ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    throw new Error(
      "The local import response does not match contract version 1.0.",
    );
  }

  if (value.state === "ready") {
    if (value.lock === null || value.failure !== null) {
      throw new Error(
        "A ready local import record must include its DatasetLock and no failure.",
      );
    }
    assertDatasetLock(value.lock);
    if (value.lock.local_path !== value.local_path) {
      throw new Error(
        "Ready local import record local_path does not match embedded DatasetLock local_path.",
      );
    }
    if (
      value.lock.provider !== "local" ||
      value.lock.dataset_id !== "local" ||
      value.lock.snapshot !== "local"
    ) {
      throw new Error(
        "A ready local import record lock must use local provider invariants.",
      );
    }
  } else if (value.state === "failed") {
    if (value.failure === null || value.lock !== null) {
      throw new Error(
        "A failed local import record must describe its failure and contain no lock.",
      );
    }
    if (
      !isRecord(value.failure) ||
      typeof value.failure.code !== "string" ||
      !value.failure.code.trim() ||
      typeof value.failure.message !== "string" ||
      !value.failure.message.trim()
    ) {
      throw new Error(
        "A failed local import record has an invalid failure payload.",
      );
    }
  } else if (value.state === "cancelled") {
    if (value.lock !== null) {
      throw new Error(
        "A cancelled local import record cannot include a DatasetLock.",
      );
    }
    if (
      value.failure === null ||
      !isRecord(value.failure) ||
      value.failure.code !== "cancelled" ||
      typeof value.failure.message !== "string"
    ) {
      throw new Error(
        "A cancelled local import record must record a cancelled failure.",
      );
    }
  } else if (value.state === "scanning") {
    if (value.lock !== null || value.failure !== null) {
      throw new Error(
        "A scanning local import record cannot include a DatasetLock or failure.",
      );
    }
  }
}

export interface LocalImportRequestOptions {
  path: string;
  token: string;
  signal?: AbortSignal;
}

export interface StartLocalImportOptions extends LocalImportRequestOptions {
  relativeDir: string;
  title: string;
  limitations?: string;
  citations?: Array<{
    title: string;
    doi?: string | null;
    url?: string | null;
  }>;
  formats?: string[];
}

/** Milliseconds between local import progress refreshes while scanning. */
export const LOCAL_IMPORT_POLL_MS = 1000;

/** Start a local/private dataset import scan for the authorized project. */
export async function startLocalImport(
  options: StartLocalImportOptions,
): Promise<LocalImportRecord> {
  requireContext(options.path, options.token);
  let response: Response;
  try {
    response = await fetch("/api/datasets/local/import", {
      method: "POST",
      headers: authHeaders(options.token),
      body: JSON.stringify({
        path: options.path,
        relative_dir: options.relativeDir,
        title: options.title,
        limitations: options.limitations ?? "",
        citations: options.citations ?? [],
        formats: options.formats ?? [],
      }),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the local import service.");
  }
  if (!response.ok)
    throw await readError(response, "Unable to start the local import");
  const payload: unknown = await response.json();
  assertLocalImportRecord(payload);
  return payload;
}

/** Read one local import record for the authorized project. */
export async function getLocalImport(
  importId: string,
  options: LocalImportRequestOptions,
): Promise<LocalImportRecord> {
  requireContext(options.path, options.token);
  const params = new URLSearchParams({ path: options.path });
  let response: Response;
  try {
    response = await fetch(
      `/api/datasets/local/imports/${encodeURIComponent(importId)}?${params.toString()}`,
      {
        headers: { Authorization: `Bearer ${options.token}` },
        signal: options.signal,
      },
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the local import service.");
  }
  if (!response.ok)
    throw await readError(response, "Unable to read the local import");
  const payload: unknown = await response.json();
  assertLocalImportRecord(payload);
  return payload;
}

/** List local import records for the authorized project. */
export async function listLocalImports(
  options: LocalImportRequestOptions,
): Promise<LocalImportRecord[]> {
  requireContext(options.path, options.token);
  const params = new URLSearchParams({ path: options.path });
  let response: Response;
  try {
    response = await fetch(`/api/datasets/local/imports?${params.toString()}`, {
      headers: { Authorization: `Bearer ${options.token}` },
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the local import service.");
  }
  if (!response.ok)
    throw await readError(response, "Unable to list local imports");
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) {
    throw new Error(
      "The local import listing does not match contract version 1.0.",
    );
  }
  payload.forEach(assertLocalImportRecord);
  return payload;
}

/** Signal a running local import scan to stop. */
export async function cancelLocalImport(
  importId: string,
  options: LocalImportRequestOptions,
): Promise<LocalImportRecord> {
  requireContext(options.path, options.token);
  let response: Response;
  try {
    response = await fetch(
      `/api/datasets/local/imports/${encodeURIComponent(importId)}/cancel`,
      {
        method: "POST",
        headers: authHeaders(options.token),
        body: JSON.stringify({ path: options.path }),
        signal: options.signal,
      },
    );
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the local import service.");
  }
  if (!response.ok)
    throw await readError(response, "Unable to cancel the local import");
  const payload: unknown = await response.json();
  assertLocalImportRecord(payload);
  return payload;
}

export async function discoverBidsEeg(
  path: string,
  relativeDir: string,
  options: { token: string; signal?: AbortSignal },
): Promise<BidsEegDiscovery> {
  requireContext(path, options.token);
  const response = await fetch("/api/datasets/bids-eeg/discover", {
    method: "POST",
    headers: authHeaders(options.token),
    signal: options.signal,
    body: JSON.stringify({ path, relative_dir: relativeDir }),
  });
  if (!response.ok)
    throw await readError(response, "Unable to inspect BIDS EEG metadata");
  const payload: unknown = await response.json();
  if (
    !isRecord(payload) ||
    payload.schema_version !== "1.0" ||
    typeof payload.dataset_path !== "string" ||
    ![
      "ready",
      "incomplete_metadata",
      "unsupported",
      "not_bids",
      "no_recordings",
    ].includes(String(payload.status)) ||
    !Array.isArray(payload.recordings) ||
    !payload.recordings.every(isBidsEegRecording) ||
    !Array.isArray(payload.issues) ||
    !payload.issues.every(isBidsEegIssue) ||
    payload.inspection_scope !== "metadata_only"
  ) {
    throw new Error(
      "The BIDS EEG discovery response does not match version 1.0.",
    );
  }
  return payload as unknown as BidsEegDiscovery;
}

export async function identifyBidsEegRecording(
  path: string,
  relativeDir: string,
  recordingPath: string,
  options: { token: string },
): Promise<BidsEegInputIdentity> {
  requireContext(path, options.token);
  const response = await fetch("/api/datasets/bids-eeg/identity", {
    method: "POST",
    headers: authHeaders(options.token),
    body: JSON.stringify({
      path,
      relative_dir: relativeDir,
      recording_path: recordingPath,
    }),
  });
  if (!response.ok)
    throw await readError(
      response,
      "Unable to create the BIDS EEG input identity",
    );
  const payload: unknown = await response.json();
  if (
    !isRecord(payload) ||
    payload.schema_version !== "1.0" ||
    typeof payload.recording_path !== "string" ||
    !["ready", "resource_limit"].includes(String(payload.status)) ||
    !(
      payload.content_identity === null ||
      typeof payload.content_identity === "string"
    ) ||
    !Array.isArray(payload.files) ||
    !payload.files.every(
      (file) =>
        isRecord(file) &&
        typeof file.path === "string" &&
        typeof file.byte_size === "number" &&
        Number.isInteger(file.byte_size) &&
        file.byte_size >= 0 &&
        typeof file.sha256 === "string" &&
        /^[0-9a-f]{64}$/.test(file.sha256),
    ) ||
    !(payload.message === null || typeof payload.message === "string") ||
    payload.inspection_scope !== "bounded_source_hashes"
  ) {
    throw new Error(
      "The BIDS EEG input identity response does not match version 1.0.",
    );
  }
  return payload as unknown as BidsEegInputIdentity;
}

export async function inspectBidsEegSignal(
  path: string,
  relativeDir: string,
  recordingPath: string,
  options: { token: string; signal?: AbortSignal },
): Promise<BidsEegSignalInspection> {
  requireContext(path, options.token);
  const response = await fetch("/api/datasets/bids-eeg/inspect", {
    method: "POST",
    headers: authHeaders(options.token),
    signal: options.signal,
    body: JSON.stringify({
      path,
      relative_dir: relativeDir,
      recording_path: recordingPath,
    }),
  });
  if (!response.ok)
    throw await readError(response, "Unable to inspect the EEG signal");
  const payload: unknown = await response.json();
  if (
    !isRecord(payload) ||
    payload.schema_version !== "1.0" ||
    typeof payload.recording_path !== "string" ||
    !["edf", "bdf", "brainvision", "eeglab"].includes(String(payload.format)) ||
    typeof payload.sampling_frequency_hz !== "number" ||
    typeof payload.sample_count !== "number" ||
    typeof payload.duration_seconds !== "number" ||
    typeof payload.channel_count !== "number" ||
    !isRecord(payload.channel_types) ||
    !Object.values(payload.channel_types).every(
      (count) => typeof count === "number",
    ) ||
    typeof payload.bad_channel_count !== "number" ||
    typeof payload.annotation_count !== "number" ||
    !Array.isArray(payload.annotation_descriptions) ||
    !payload.annotation_descriptions.every(
      (item) => typeof item === "string",
    ) ||
    !(
      payload.highpass_hz === null || typeof payload.highpass_hz === "number"
    ) ||
    !(payload.lowpass_hz === null || typeof payload.lowpass_hz === "number") ||
    payload.inspection_scope !== "read_only_signal_metadata"
  ) {
    throw new Error(
      "The BIDS EEG signal inspection response does not match version 1.0.",
    );
  }
  return payload as unknown as BidsEegSignalInspection;
}

export async function previewBidsEegSignal(
  path: string,
  relativeDir: string,
  recordingPath: string,
  options: {
    token: string;
    timeStartSeconds?: number;
    durationSeconds?: number;
    channelNames?: string[];
    maxBuckets?: number;
    signal?: AbortSignal;
  },
): Promise<BidsEegSignalPreview> {
  requireContext(path, options.token);
  const response = await fetch("/api/datasets/bids-eeg/preview", {
    method: "POST",
    headers: authHeaders(options.token),
    signal: options.signal,
    body: JSON.stringify({
      path,
      relative_dir: relativeDir,
      recording_path: recordingPath,
      time_start_seconds: options.timeStartSeconds ?? 0,
      duration_seconds: options.durationSeconds ?? 10,
      channel_names: options.channelNames ?? [],
      max_buckets: options.maxBuckets ?? 400,
    }),
  });
  if (!response.ok)
    throw await readError(response, "Unable to create the EEG signal preview");
  const payload: unknown = await response.json();
  const isFiniteNumber = (value: unknown): value is number =>
    typeof value === "number" && Number.isFinite(value);
  if (
    !isRecord(payload) ||
    payload.schema_version !== "1.0" ||
    typeof payload.recording_path !== "string" ||
    typeof payload.source_content_identity !== "string" ||
    !isFiniteNumber(payload.time_start_seconds) ||
    !isFiniteNumber(payload.duration_seconds) ||
    !isFiniteNumber(payload.sampling_frequency_hz) ||
    !Number.isInteger(payload.sample_count) ||
    !Array.isArray(payload.channels) ||
    !payload.channels.every(
      (channel) =>
        isRecord(channel) &&
        typeof channel.name === "string" &&
        typeof channel.channel_type === "string" &&
        typeof channel.marked_bad === "boolean",
    ) ||
    !Array.isArray(payload.traces) ||
    !payload.traces.every(
      (trace) =>
        isRecord(trace) &&
        typeof trace.channel_name === "string" &&
        trace.unit === "µV" &&
        Array.isArray(trace.bins) &&
        trace.bins.every(
          (bin) =>
            isRecord(bin) &&
            isFiniteNumber(bin.time_seconds) &&
            isFiniteNumber(bin.minimum_uv) &&
            isFiniteNumber(bin.maximum_uv),
        ),
    ) ||
    !Array.isArray(payload.events) ||
    !payload.events.every(
      (event) =>
        isRecord(event) &&
        isFiniteNumber(event.onset_seconds) &&
        isFiniteNumber(event.duration_seconds) &&
        typeof event.description === "string",
    ) ||
    !Number.isInteger(payload.event_count) ||
    typeof payload.events_truncated !== "boolean" ||
    !isRecord(payload.spectrum) ||
    payload.spectrum.method !== "welch" ||
    payload.spectrum.window !== "hamming" ||
    !Number.isInteger(payload.spectrum.n_fft) ||
    !Number.isInteger(payload.spectrum.n_per_seg) ||
    !Number.isInteger(payload.spectrum.n_overlap) ||
    payload.spectrum.reject_by_annotation !== false ||
    payload.spectrum.unit !== "µV²/Hz" ||
    !Array.isArray(payload.spectrum.frequencies_hz) ||
    !payload.spectrum.frequencies_hz.every(isFiniteNumber) ||
    !Array.isArray(payload.spectrum.traces) ||
    !payload.spectrum.traces.every(
      (trace) =>
        isRecord(trace) &&
        typeof trace.channel_name === "string" &&
        Array.isArray(trace.power_uv2_per_hz) &&
        trace.power_uv2_per_hz.every(isFiniteNumber),
    ) ||
    payload.preview_scope !== "bounded_read_only_signal_preview"
  ) {
    throw new Error(
      "The BIDS EEG signal preview response does not match version 1.0.",
    );
  }
  return payload as unknown as BidsEegSignalPreview;
}

function assertResearcherDecisions(value: unknown): asserts value is {
  schema_version: "1.0";
  records: ResearcherDecision[];
} {
  if (
    !isRecord(value) ||
    value.schema_version !== "1.0" ||
    !Array.isArray(value.records) ||
    !value.records.every(
      (item) =>
        isRecord(item) &&
        item.schema_version === "1.0" &&
        typeof item.id === "string" &&
        typeof item.researcher === "string" &&
        typeof item.dataset_path === "string" &&
        typeof item.recording_path === "string" &&
        typeof item.source_content_identity === "string" &&
        ["accepted", "rejected", "needs_review"].includes(
          String(item.decision),
        ) &&
        typeof item.note === "string" &&
        Array.isArray(item.channel_names) &&
        item.channel_names.every((name) => typeof name === "string") &&
        (item.time_start_seconds === null ||
          typeof item.time_start_seconds === "number") &&
        (item.time_end_seconds === null ||
          typeof item.time_end_seconds === "number") &&
        typeof item.created_at === "string" &&
        typeof item.updated_at === "string",
    )
  )
    throw new Error(
      "The researcher decision response does not match version 1.0.",
    );
}

export async function listResearcherDecisions(
  path: string,
  datasetPath: string,
  recordingPath: string,
  sourceContentIdentity: string,
  token: string,
): Promise<ResearcherDecision[]> {
  requireContext(path, token);
  const response = await fetch("/api/datasets/bids-eeg/decisions", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({
      path,
      dataset_path: datasetPath,
      recording_path: recordingPath,
      source_content_identity: sourceContentIdentity,
    }),
  });
  if (!response.ok)
    throw await readError(response, "Unable to load researcher decisions");
  const payload: unknown = await response.json();
  assertResearcherDecisions(payload);
  return payload.records;
}

export async function createResearcherDecision(
  path: string,
  values: Omit<
    ResearcherDecision,
    "schema_version" | "id" | "created_at" | "updated_at"
  >,
  token: string,
): Promise<ResearcherDecision> {
  requireContext(path, token);
  const response = await fetch("/api/datasets/bids-eeg/decisions/create", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ path, ...values }),
  });
  if (!response.ok)
    throw await readError(response, "Unable to save researcher decision");
  const payload: unknown = await response.json();
  assertResearcherDecisions({ schema_version: "1.0", records: [payload] });
  return payload as ResearcherDecision;
}

/** Reconcile local import records after a service restart. */
export async function recoverLocalImports(
  options: LocalImportRequestOptions,
): Promise<LocalImportRecord[]> {
  requireContext(options.path, options.token);
  let response: Response;
  try {
    response = await fetch("/api/datasets/local/recover", {
      method: "POST",
      headers: authHeaders(options.token),
      body: JSON.stringify({ path: options.path }),
      signal: options.signal,
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError")
      throw error;
    throw new Error("Unable to reach the local import service.");
  }
  if (!response.ok)
    throw await readError(response, "Unable to recover local imports");
  const payload: unknown = await response.json();
  if (!Array.isArray(payload)) {
    throw new Error(
      "The local import listing does not match contract version 1.0.",
    );
  }
  payload.forEach(assertLocalImportRecord);
  return payload;
}

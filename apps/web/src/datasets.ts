import type {
  CatalogEntry,
  DatasetListItem,
  DatasetListResponse,
} from "./types";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
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

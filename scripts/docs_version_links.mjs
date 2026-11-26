/** Match a version's exact status page URL, rejecting tag-prefix collisions. */
export function matchesExactStatusPage(statusLink, versionUrl) {
  if (typeof statusLink !== "string" || statusLink.length === 0) return false;

  try {
    const expectedStatusUrl = new URL("status.html", versionUrl);
    const actualStatusUrl = new URL(statusLink, versionUrl);
    return actualStatusUrl.href === expectedStatusUrl.href;
  } catch {
    return false;
  }
}

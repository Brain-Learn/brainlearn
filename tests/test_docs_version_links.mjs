import assert from "node:assert/strict";
import test from "node:test";

import { matchesExactStatusPage } from "../scripts/docs_version_links.mjs";

const versionUrl =
  "https://brain-learn.github.io/brainlearn/versions/v0.1.0a1/";
const expectedStatusUrl =
  "https://brain-learn.github.io/brainlearn/versions/v0.1.0a1/status.html";

test("accepts the exact absolute status page for an archived version", () => {
  assert.equal(matchesExactStatusPage(expectedStatusUrl, versionUrl), true);
});

test("accepts a relative link to the same version-specific status page", () => {
  assert.equal(matchesExactStatusPage("status.html", versionUrl), true);
});

test("rejects another tag whose name starts with the archived tag", () => {
  assert.equal(
    matchesExactStatusPage(
      "https://brain-learn.github.io/brainlearn/versions/v0.1.0a1-evil/status.html",
      versionUrl,
    ),
    false,
  );
});

test("rejects a status page hosted on another origin", () => {
  assert.equal(
    matchesExactStatusPage(
      "https://attacker.example/brainlearn/versions/v0.1.0a1/status.html",
      versionUrl,
    ),
    false,
  );
});

test("rejects query and fragment changes from the exact status URL", () => {
  assert.equal(
    matchesExactStatusPage(`${expectedStatusUrl}?redirect=1`, versionUrl),
    false,
  );
  assert.equal(
    matchesExactStatusPage(`${expectedStatusUrl}#current`, versionUrl),
    false,
  );
});

test("rejects missing and malformed status links", () => {
  assert.equal(matchesExactStatusPage(null, versionUrl), false);
  assert.equal(matchesExactStatusPage("http://[broken", versionUrl), false);
});

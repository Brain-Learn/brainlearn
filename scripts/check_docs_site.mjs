import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, resolve, sep } from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const repositoryRoot = resolve(fileURLToPath(new URL("..", import.meta.url)));
const siteRoot = resolve(process.argv[2] ?? join(repositoryRoot, "_build", "html"));
const docsRequire = createRequire(new URL("../website/package.json", import.meta.url));
const { chromium } = docsRequire("playwright");
const axeScript = docsRequire.resolve("axe-core/axe.min.js");
const mimeTypes = new Map([
  [".css", "text/css; charset=utf-8"],
  [".html", "text/html; charset=utf-8"],
  [".js", "text/javascript; charset=utf-8"],
  [".json", "application/json; charset=utf-8"],
  [".png", "image/png"],
  [".svg", "image/svg+xml"],
  [".webp", "image/webp"],
  [".woff2", "font/woff2"],
]);

await stat(join(siteRoot, "index.html"));

const server = createServer(async (request, response) => {
  try {
    const pathname = decodeURIComponent(new URL(request.url, "http://127.0.0.1").pathname);
    const requestedPath = pathname.endsWith("/") ? `${pathname}index.html` : pathname;
    const filePath = resolve(siteRoot, `.${requestedPath}`);
    if (filePath !== siteRoot && !filePath.startsWith(`${siteRoot}${sep}`)) {
      response.writeHead(403).end("Forbidden");
      return;
    }
    const contents = await readFile(filePath);
    response.writeHead(200, { "content-type": mimeTypes.get(extname(filePath)) ?? "application/octet-stream" });
    response.end(contents);
  } catch {
    response.writeHead(404).end("Not found");
  }
});

await new Promise((resolveListen, rejectListen) => {
  server.once("error", rejectListen);
  server.listen(0, "127.0.0.1", resolveListen);
});

const address = server.address();
const baseUrl = `http://127.0.0.1:${address.port}`;
const browser = await chromium.launch({ headless: true });
const routes = [
  "/index.html",
  "/quickstart.html",
  "/auto_examples/index.html",
  "/auto_examples/plot_synthetic_filter.html",
  "/api.html",
  "/status.html",
  "/installation.html",
];
const viewports = [
  { name: "mobile", width: 375, height: 812 },
  { name: "desktop", width: 1440, height: 1000 },
];
const failures = [];

try {
  const statusPage = await browser.newPage({ viewport: { width: 1440, height: 900 } });
  await statusPage.goto(`${baseUrl}/index.html`, { waitUntil: "domcontentloaded" });
  const expectedStatus = process.env.BRAINLEARN_DOCS_STATUS ?? "Development";
  const statusText = await statusPage.locator(".bd-header-announcement .bl-status").innerText();
  if (!statusText.includes(expectedStatus) || !statusText.includes("docs")) {
    failures.push(`version label: expected ${expectedStatus} documentation status, received "${statusText}"`);
  }
  await statusPage.locator('button[aria-label="Search"]').first().click();
  const searchInput = statusPage.getByPlaceholder("Search the docs ...");
  await searchInput.fill("quality control");
  await statusPage.keyboard.press("Enter");
  await statusPage.locator("main").getByText("Search Results").waitFor();
  const searchText = await statusPage.locator("main").innerText();
  if (!searchText.includes("Quality control and researcher decisions")) {
    failures.push("site search: expected the quality-control guide in results for 'quality control'");
  }
  await statusPage.close();
  process.stdout.write(`Checked search and ${expectedStatus.toLowerCase()} version label\n`);

  for (const route of routes) {
    for (const viewport of viewports) {
      const page = await browser.newPage({ viewport: { width: viewport.width, height: viewport.height } });
      await page.goto(`${baseUrl}${route}`, { waitUntil: "load" });
      await page.locator("main").waitFor();
      const layout = await page.evaluate(() => ({
        viewportWidth: window.innerWidth,
        documentWidth: document.documentElement.scrollWidth,
        title: document.title,
        hasMainHeading: Boolean(document.querySelector("main h1")),
        brokenImages: [...document.querySelectorAll("main img")]
          .filter((image) => !image.complete || image.naturalWidth === 0)
          .map((image) => image.getAttribute("src")),
      }));
      if (layout.documentWidth > layout.viewportWidth + 1) {
        failures.push(`${route} at ${viewport.name}: horizontal overflow ${layout.documentWidth}px > ${layout.viewportWidth}px`);
      }
      if (!layout.title || !layout.hasMainHeading) {
        failures.push(`${route} at ${viewport.name}: missing page title or main heading`);
      }
      if (layout.brokenImages.length > 0) {
        failures.push(`${route} at ${viewport.name}: images failed to load (${layout.brokenImages.join(", ")})`);
      }

      await page.addScriptTag({ path: axeScript });
      const accessibility = await page.evaluate(async () => {
        const results = await window.axe.run(document, {
          runOnly: { type: "tag", values: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"] },
        });
        return results.violations.map((violation) => ({
          id: violation.id,
          impact: violation.impact,
          description: violation.help,
          selectors: violation.nodes.map((node) => node.target.join(", ")),
        }));
      });
      for (const violation of accessibility) {
        failures.push(`${route} at ${viewport.name}: axe ${violation.impact} ${violation.id} (${violation.description}) on ${violation.selectors.join("; ")}`);
      }
      await page.close();
      process.stdout.write(`Checked ${route} at ${viewport.name} (${viewport.width}px)\n`);
    }
  }
} finally {
  await browser.close();
  await new Promise((resolveClose, rejectClose) => server.close((error) => error ? rejectClose(error) : resolveClose()));
}

if (failures.length > 0) {
  process.stderr.write(`${failures.map((failure) => `- ${failure}`).join("\n")}\n`);
  process.exitCode = 1;
} else {
  process.stdout.write(`Passed responsive and axe-core checks for ${routes.length} pages at ${viewports.length} viewport sizes.\n`);
}

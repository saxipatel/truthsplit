import path from "node:path";

import { expect, test } from "@playwright/test";

import { ROOT } from "./helpers";

// Run with SCREENSHOTS=1 (make screenshots). Captures the real UI against the running stack.
test.skip(!process.env.SCREENSHOTS, "screenshots are captured only on request");

const OUT = path.join(ROOT, "docs", "screenshots");

test("capture screenshots", async ({ page, request }) => {
  test.setTimeout(120_000);
  const demo = await (await request.post("/api/demo")).json();
  const [a, b] = [demo.A.investigation_id, demo.B.investigation_id];
  await page.setViewportSize({ width: 1280, height: 900 });
  const shots: [string, string, string][] = [
    ["01-overview.png", "/", "Reconciliation status"],
    ["02-autopsy-fully-explained.png", `/investigations/${a}?tab=autopsy`, "Reconciliation waterfall"],
    ["03-autopsy-unexplained-remainder.png", `/investigations/${b}?tab=autopsy`, "Evidence breakdown"],
    ["04-what-if-simulator.png", `/investigations/${a}?tab=simulator`, "Saved simulations"],
    ["05-comparison-results.png", `/investigations/${a}?tab=comparison`, "Metric DNA"],
    ["06-review-and-history.png", `/investigations/${b}?tab=review`, "Similar investigations"],
    ["07-metric-definitions.png", "/metrics", "Compare two definitions"],
    ["08-sources-databases.png", "/sources?tab=databases", "Snapshot a registered query"],
    ["09-history.png", "/history", "Recurring discrepancy patterns"],
  ];
  for (const [file, url, marker] of shots) {
    await page.goto(url);
    await expect(page.getByText(marker).first()).toBeVisible();
    await page.waitForLoadState("networkidle");
    await page.screenshot({ path: path.join(OUT, file), fullPage: true });
  }
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/investigations/${a}?tab=autopsy`);
  await expect(page.getByText("Reconciliation waterfall")).toBeVisible();
  await page.waitForLoadState("networkidle");
  await page.screenshot({ path: path.join(OUT, "10-mobile-autopsy.png"), fullPage: false });
});

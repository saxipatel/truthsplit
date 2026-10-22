import path from "node:path";

import { expect, test } from "@playwright/test";

const FIXTURES = path.join(__dirname, "..", "fixtures");

// e2e_left.csv sums to 150.00; e2e_right.csv to 180.00 (K2 exported twice, K5 missing, K6 extra).
test("import two files, investigate, inspect evidence, simulate, export", async ({ page }) => {
  const stamp = Date.now();
  await page.goto("/investigations/new");

  // Required fields are validated before anything is sent.
  await page.getByRole("button", { name: "Run investigation" }).click();
  await expect(page.getByText("Give the investigation a title.")).toBeVisible();
  await expect(page.getByText("Choose or create the left dataset.")).toBeVisible();

  await page.getByLabel("Title").fill(`E2E file investigation ${stamp}`);

  for (const [side, file] of [
    ["2. Left input", "e2e_left.csv"],
    ["3. Right input", "e2e_right.csv"],
  ] as const) {
    const card = page.locator("section").filter({ has: page.getByRole("heading", { name: side }) });
    await card.getByRole("tab", { name: "Upload a file" }).click();
    await card.getByLabel("File (CSV, XLSX or XLS)").setInputFiles(path.join(FIXTURES, file));
    await card.getByRole("button", { name: "Upload and preview" }).click();
    await expect(card.getByRole("table", { name: "File preview" })).toBeVisible();
    await expect(card.getByRole("checkbox", { name: "order_id" }).first()).toBeChecked(); // key guessed from the header
    await card.getByRole("button", { name: "Import dataset" }).click();
    await expect(card.getByText(/rows accepted/)).toBeVisible();
    await expect(card.getByText(/^Selected:/)).toBeVisible();
  }

  const metrics = page.locator("section").filter({ has: page.getByRole("heading", { name: "4. Metric definitions" }) });
  await metrics.getByRole("button", { name: "+ New definition" }).first().click();
  await metrics.getByLabel("Name").fill(`E2E gross ${stamp}`);
  await metrics.getByRole("button", { name: "Save definition" }).click();
  await expect(page.getByLabel("Left metric definition")).not.toHaveValue("");
  const chosen = await page.getByLabel("Left metric definition").inputValue();
  await page.getByLabel("Right metric definition").selectOption(chosen);
  await expect(page.getByText("The definitions are identical")).toBeVisible();

  await page.getByRole("button", { name: "Run investigation" }).click();
  await expect(page).toHaveURL(/\/investigations\/[0-9a-f-]{36}/);
  const difference = page.getByRole("group", { name: "Difference (right − left)" });
  await expect(difference).toContainText("+30.00");

  // Findings expand to show record-level evidence.
  await page.getByRole("button", { name: /keys contribute only on the left/ }).click();
  await expect(page.getByRole("table", { name: /Evidence for/ })).toContainText("K5");

  // Simulate removing the exported-twice line; the stored baseline must not change.
  await page.getByRole("tab", { name: "What-if simulator" }).click();
  await page.getByLabel("Which rows").selectOption("exact");
  await page.getByRole("button", { name: "Run simulation" }).click();
  await expect(page.getByRole("group", { name: "Difference after" })).toContainText("+10.00");
  await expect(page.getByRole("group", { name: "Gap closed" })).toContainText("+20.00");
  await page.reload();
  await expect(difference).toContainText("+30.00");
  await expect(page.getByRole("table", { name: "Saved simulations" })).toContainText("+10.00");

  // Reports download.
  await page.getByRole("tab", { name: "Report" }).click();
  const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("link", { name: "Download HTML" }).click()]);
  expect(download.suggestedFilename()).toMatch(/^truthsplit-.*\.html$/);
  const json = await page.request.get(page.url().replace(/\/investigations\/([^?]+).*/, "/api/investigations/$1/export"));
  const report = await json.json();
  expect(report.totals.delta).toBe("30.0000");
  expect(report.simulations).toHaveLength(1);
});

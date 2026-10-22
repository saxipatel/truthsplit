import { expect, test } from "@playwright/test";

import { scenario } from "./helpers";

const scenarioA = scenario("A");

test("investigate two database query snapshots", async ({ page, request }) => {
  expect((await request.post("/api/demo")).ok()).toBeTruthy();
  await page.goto("/investigations/new");
  await page.getByLabel("Title").fill(`E2E database investigation ${Date.now()}`);
  for (const [side, key] of [
    ["2. Left input", "finance_order_revenue"],
    ["3. Right input", "sales_order_payments"],
  ] as const) {
    const card = page.locator("section").filter({ has: page.getByRole("heading", { name: side }) });
    await card.getByRole("tab", { name: "Database query" }).click();
    await card.getByLabel("Registered query").selectOption(key);
    await card.getByLabel("First day included").fill("2026-03-01");
    await card.getByLabel("First day excluded").fill("2026-04-01");
    await card.getByRole("button", { name: "Run and save snapshot" }).click();
    await expect(card.getByText(/^Selected:/)).toBeVisible();
  }
  const metrics: { id: string; name: string; version: number }[] = await (await request.get("/api/metric-definitions")).json();
  const idOf = (name: string) => metrics.find((m) => m.name === name && m.version === 1)!.id;
  await page.getByLabel("Left metric definition").selectOption(idOf(scenarioA.left.metric.name));
  await page.getByLabel("Right metric definition").selectOption(idOf(scenarioA.right.metric.name));
  await page.getByRole("button", { name: "Run investigation" }).click();
  await expect(page).toHaveURL(/\/investigations\/[0-9a-f-]{36}/);
  // 155,997.39 per the demo manifest (computed independently of the engine).
  const expected = Number(scenarioA.expected.delta).toLocaleString("en-US", { minimumFractionDigits: 2 });
  await expect(page.getByRole("group", { name: "Difference (right − left)" })).toContainText(`+${expected}`);
  await expect(page.getByRole("group", { name: "Unexplained", exact: true })).toContainText("0.00");
});

test("review decisions persist across reloads", async ({ page, request }) => {
  const demo = await (await request.post("/api/demo")).json();
  const id = demo.B.investigation_id;
  await page.goto(`/investigations/${id}?tab=review`);
  await page.getByLabel("Decision").selectOption("comment");
  const text = `E2E note ${Date.now()}`;
  await page.getByLabel("Note", { exact: true }).fill(text);
  await page.getByRole("button", { name: "Save note" }).click();
  await expect(page.getByText(text)).toBeVisible();
  await page.reload();
  await expect(page.getByText(text)).toBeVisible();
  await expect(page.getByText(/Comment note/).first()).toBeVisible();
});

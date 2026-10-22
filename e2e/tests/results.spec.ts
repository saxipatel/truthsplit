import { readFileSync } from "node:fs";
import path from "node:path";

import { expect, test, type Page } from "@playwright/test";

import { money, parseCsv, ROOT, scenario, units } from "./helpers";

let ids: Record<string, string>;

test.beforeAll(async ({ request }) => {
  const demo = await (await request.post("/api/demo")).json();
  ids = Object.fromEntries(Object.entries(demo).map(([k, v]) => [k, (v as { investigation_id: string }).investigation_id]));
});

async function waterfall(page: Page) {
  const table = page.getByRole("table", { name: "Waterfall steps" });
  await expect(table).toBeVisible();
  const rows = await table.locator("tbody tr").all();
  return Promise.all(rows.map(async (r) => (await r.locator("td").allInnerTexts()).map((t) => t.trim())));
}

for (const sid of ["A", "B", "C"]) {
  test(`waterfall for demo ${sid} matches the manifest`, async ({ page }) => {
    const sc = scenario(sid);
    await page.goto(`/investigations/${ids[sid]}?tab=autopsy`);
    const rows = await waterfall(page);
    const steps = rows.slice(0, -1);
    const total = rows[rows.length - 1];

    // Expected attributed amounts: confirmed issues with an amount, plus the unexplained remainder, plus (for B)
    // the record-level mismatch that also carries the unattributed fan-out copies.
    const expected: string[] = sc.issues
      .filter((i: { expected_status: string; expected_contribution: string | null; category: string }) => i.expected_contribution !== null && !(sid === "B" && i.category === "amount_mismatch"))
      .map((i: { expected_contribution: string }) => money(i.expected_contribution));
    if (sid === "B") expected.push(money(sc.expected.amount_mismatch_finding));
    expect(steps.map((r) => r[3]).sort()).toEqual(expected.sort());

    // Running totals: start at 0, each row's "after" = "before" + change, the last equals the difference.
    let running = 0n;
    for (const [, , before, change, after] of steps) {
      expect(units(before)).toBe(running);
      running += units(change);
      expect(units(after)).toBe(running);
    }
    expect(running).toBe(units(sc.expected.delta));
    expect(total[4]).toBe(money(sc.expected.delta));
    await expect(page.getByText("Totals do not reconcile")).toHaveCount(0);
  });
}

test("evidence breakdown totals by status (demo B)", async ({ page }) => {
  const exp = scenario("B").expected;
  await page.goto(`/investigations/${ids.B}?tab=autopsy`);
  const confirmed = units(exp.delta) - units(exp.unexplained);
  await expect(page.getByRole("group", { name: "Confirmed findings" })).toContainText(money(confirmed === 0n ? "0" : `${confirmed / 10000n}.${(confirmed % 10000n).toString().padStart(4, "0")}`));
  await expect(page.getByRole("group", { name: "Suspected findings" })).toContainText("Not attributed");
  await expect(page.getByRole("group", { name: "Unexplained findings" })).toContainText(money(exp.unexplained));
  await expect(page.getByRole("heading", { name: /Suspected findings/ })).toBeVisible();
  // Supporting records and the calculation are reachable from a finding card.
  await page.getByRole("button", { name: /Possible join amplification/ }).click();
  await expect(page.getByText("Indicative excess").first().or(page.getByText("Excess").first())).toBeVisible();
  await expect(page.getByRole("table", { name: /Evidence for Possible join amplification/ })).toContainText("not attributed");
});

test("evidence status and review status are separate", async ({ page }) => {
  await page.goto(`/investigations/${ids.A}`);
  await expect(page.getByText("Fully explained").first()).toBeVisible();
  await page.goto(`/investigations/${ids.B}?tab=review`);
  await expect(page.getByText("Unexplained remainder").first()).toBeVisible();
  // Resolve B formally: the review status changes, the evidence status does not.
  await page.getByLabel("Move to").selectOption("resolved");
  await page.getByLabel("Resolution note (required)").fill("Accounting confirmed the manual journal lines.");
  await page.getByRole("button", { name: "Change status" }).click();
  await expect(page.locator("header, h1").first()).toBeVisible();
  await expect(page.getByTitle("Review status, set by a person").first()).toHaveText("Resolved");
  await expect(page.getByTitle(/Computed from the evidence/).first()).toContainText("Unexplained remainder");
  // Restore the demo state.
  await page.getByLabel("Move to").selectOption("open");
  await page.getByRole("button", { name: "Change status" }).click();
  await expect(page.getByTitle("Review status, set by a person").first()).toHaveText("Open");
});

test("saved simulations appear in the before/after chart with their exact values", async ({ page, request }) => {
  const rule = { rule_type: "remove_duplicates", side: "right", kind: "join_copies" };
  const sim = await (await request.post(`/api/investigations/${ids.A}/simulations`, { data: rule })).json();
  const saved = await (await request.get(`/api/investigations/${ids.A}/simulations`)).json();
  await page.goto(`/investigations/${ids.A}?tab=simulator`);
  const chart = page.locator("figure").filter({ hasText: "Difference before (stored baseline)" });
  await expect(chart).toBeVisible();
  await expect(chart.locator(".recharts-yAxis .recharts-cartesian-axis-tick")).toHaveCount(saved.length);
  const table = page.getByRole("table", { name: "Saved simulations" });
  const last = table.locator("tbody tr").nth(saved.length - 1);
  await expect(last).toContainText(money(sim.after_delta));
  await expect(last).toContainText(money(scenario("A").expected.delta));
});

test("dataset profile matches the settlement CSV", async ({ page, request }) => {
  const rows = parseCsv(readFileSync(path.join(ROOT, "sample_data", "settlement_march.csv"), "utf8"));
  const sum = rows.reduce((s, r) => s + units(r.net_amount), 0n);
  const refunded = rows.filter((r) => units(r.refunded) > 0n).length;
  const zero = rows.filter((r) => units(r.net_amount) === 0n).length;
  const datasets: { id: string; label: string }[] = await (await request.get("/api/datasets")).json();
  const ds = datasets.find((d) => d.label === "settlement_march.csv")!;
  await page.goto("/sources");
  await page.getByRole("row", { name: /settlement_march\.csv/ }).getByRole("button", { name: "Profile" }).click();
  const profile = page.getByLabel(`Profile of ${ds.label}`);
  await expect(profile).toContainText(`Rows stored${rows.length.toLocaleString("en-US")}`);
  await expect(profile).toContainText(money(`${sum / 10000n}.${(sum % 10000n).toString().padStart(4, "0")}`, false));
  await expect(profile).toContainText(`${refunded} (sum`);
  await expect(profile).toContainText(`0 / ${zero} / 0`);
  const first = rows.map((r) => new Date(r.settled_at).toISOString()).sort()[0];
  await expect(profile).toContainText(`Time range${first.slice(0, 16).replace("T", " ")} UTC`);
});

test("metric DNA comparison explains each differing rule", async ({ page, request }) => {
  const stamp = Date.now();
  const left = await (await request.post("/api/metric-definitions", { data: { name: `E2E net NY ${stamp}`, aggregation: "sum", refund_policy: "net_of_refunds", grain: "business_key", window_start: "2026-03-01T00:00:00", window_end: "2026-04-01T00:00:00", timezone: "America/New_York", filters: [{ field: "status", op: "ne", value: "void" }] } })).json();
  const right = await (await request.post("/api/metric-definitions", { data: { name: `E2E gross UTC ${stamp}`, aggregation: "sum", window_start: "2026-03-01T00:00:00", window_end: "2026-04-01T00:00:00" } })).json();
  await page.goto("/metrics");
  await page.getByLabel("Left", { exact: true }).selectOption(left.id);
  await page.getByLabel("Right", { exact: true }).selectOption(right.id);
  const list = page.getByRole("list", { name: "Definition differences" });
  await expect(list.locator("li")).toHaveCount(4);
  await expect(list).toContainText("Refund treatment differs. Left is net of refunds; right is gross");
  await expect(list).toContainText("Grain differs");
  await expect(list).toContainText("Only on the left: status ne void");
  await expect(list).toContainText("2026-03-01T05:00:00+00:00"); // New York midnight in UTC
});

test("overview KPIs count evidence and review states separately", async ({ page, request }) => {
  const all: { status: string; baseline_delta: string; unexplained_delta: string }[] = await (await request.get("/api/investigations?limit=200")).json();
  const fully = all.filter((i) => units(i.unexplained_delta) === 0n && units(i.baseline_delta) !== 0n).length;
  const remainder = all.filter((i) => units(i.unexplained_delta) !== 0n).length;
  const resolved = all.filter((i) => i.status === "resolved").length;
  await page.goto("/");
  await expect(page.getByRole("group", { name: "Fully explained" })).toContainText(String(fully));
  await expect(page.getByRole("group", { name: "With an unexplained remainder" })).toContainText(String(remainder));
  await expect(page.getByRole("group", { name: "Formally resolved" })).toContainText(String(resolved));
  await expect(page.getByRole("table", { name: "Recurring discrepancies" })).toContainText("Join amplification");
});

import { expect, test } from "@playwright/test";

const PAGES = [
  ["/", "Overview"],
  ["/investigations/new", "New investigation"],
  ["/sources", "Sources"],
  ["/sources?tab=databases", "Sources"],
  ["/metrics", "Metric definitions"],
  ["/history", "Investigation history"],
  ["/settings", "Settings"],
] as const;

test.beforeAll(async ({ request }) => {
  // The demo investigations give every page real content; loading them twice is a no-op.
  expect((await request.post("/api/demo")).ok()).toBeTruthy();
});

for (const [path, heading] of PAGES) {
  test(`${path} loads without errors`, async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (e) => errors.push(e.message));
    page.on("console", (m) => m.type() === "error" && errors.push(m.text()));
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
    await expect(page.getByRole("alert")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

test("pages and investigation tabs have no horizontal overflow at 375px", async ({ page, request }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  const investigations = await (await request.get("/api/investigations")).json();
  const id = investigations[0].id;
  const paths = [...PAGES.map(([p]) => p), ...["comparison", "autopsy", "simulator", "review", "report"].map((t) => `/investigations/${id}?tab=${t}`)];
  for (const path of paths) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await page.waitForLoadState("networkidle");
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
});

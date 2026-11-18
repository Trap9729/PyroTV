import { expect, test, type Page } from "@playwright/test";
import { unlinkSync, writeFileSync } from "node:fs";
import path from "node:path";

const SHOP = "Testbutiken";
const MANUFACTURER = "Lesli";
const UNTICKED = "Blå Måne"; // a Lesli piece we switch off in the wizard
const KEPT = "Åska 49"; // another Lesli piece that must stay
const OTHER_MANUFACTURER_PIECE = "Gyllene Regn 100"; // Nico, not selected

const dataDir = process.env.PYROTV_E2E_DATA!;
const fakeTv = path.join(dataDir, "fake_tv");

test.describe.configure({ mode: "serial" });

const tile = (page: Page, name: string) => page.locator(".tile", { hasText: name });
const video = (page: Page) => page.locator("video.player-video");
const currentTime = (page: Page) => video(page).evaluate((v: HTMLVideoElement) => v.currentTime);

async function tvConnected(page: Page): Promise<boolean> {
  const res = await page.request.get("/api/state");
  return (await res.json()).tv_connected;
}

async function doubleTap(page: Page, side: "left" | "right") {
  const box = (await page.locator(".gesture-layer").boundingBox())!;
  const x = box.x + box.width * (side === "right" ? 0.75 : 0.25);
  const y = box.y + box.height * 0.4; // clear of the control bar and exit button
  await page.mouse.click(x, y);
  await page.mouse.click(x, y);
}

test("kiosk smoke: wizard, grid, player, settings, TV mode", async ({ page }) => {
  const problems: string[] = [];
  page.on("pageerror", (e) => problems.push(`pageerror: ${e.message}`));
  page.on("console", (m) => {
    // The app ships no favicon, so the browser's automatic request 404s; not an app error.
    if (m.location().url.endsWith("/favicon.ico")) return;
    if (m.type() === "error") problems.push(`console.error: ${m.text()} @ ${m.location().url}`);
  });
  page.on("response", (r) => {
    if (r.status() >= 400 && !r.url().endsWith("/favicon.ico")) problems.push(`http ${r.status()}: ${r.url()}`);
  });

  // 1. wizard ---------------------------------------------------------------
  await page.goto("/");
  await expect(page).toHaveURL(/#\/wizard/);
  await page.getByLabel("Butikens namn").fill(SHOP);
  await page.getByRole("button", { name: "Nästa" }).click();

  // step 2: exactly one manufacturer
  await page.getByRole("button", { name: "Välj inga" }).click();
  await page.getByRole("button", { name: new RegExp(`^${MANUFACTURER}`) }).click();
  await expect(page.locator('.wiz-mfr[aria-pressed="true"]')).toHaveCount(1);
  await page.getByRole("button", { name: "Nästa" }).click();

  // step 3: untick one piece
  const row = page.getByRole("checkbox", { name: UNTICKED });
  await expect(row).toHaveAttribute("aria-checked", "true");
  await row.click();
  await expect(row).toHaveAttribute("aria-checked", "false");
  await expect(page.getByRole("checkbox", { name: KEPT })).toHaveAttribute("aria-checked", "true");
  await page.getByRole("button", { name: "Nästa" }).click();

  // step 4 (custom): skip, step 5: finish
  await expect(page.getByRole("heading", { name: "Egna fyrverkerier" })).toBeVisible();
  await page.getByRole("button", { name: "Nästa" }).click();
  await page.getByRole("button", { name: "Klar" }).click();

  // 2. grid -----------------------------------------------------------------
  await expect(page.locator(".shop-name")).toHaveText(SHOP);
  await expect(tile(page, KEPT)).toBeVisible();
  await expect(tile(page, UNTICKED)).toHaveCount(0);
  await expect(tile(page, OTHER_MANUFACTURER_PIECE)).toHaveCount(0);

  // 3. player ---------------------------------------------------------------
  await tile(page, KEPT).click();
  await expect(video(page)).toBeVisible();
  await expect
    .poll(() => video(page).evaluate((v: HTMLVideoElement) => !v.paused && v.readyState >= 2), {
      timeout: 20_000,
    })
    .toBe(true);
  await expect.poll(() => currentTime(page), { timeout: 20_000 }).toBeGreaterThan(2);
  const t1 = await currentTime(page);
  await expect.poll(() => currentTime(page), { timeout: 10_000 }).toBeGreaterThan(t1);

  const beforeRight = await currentTime(page);
  await doubleTap(page, "right");
  await expect
    .poll(async () => (await currentTime(page)) - beforeRight, { timeout: 5_000 })
    .toBeGreaterThanOrEqual(8);
  expect((await currentTime(page)) - beforeRight).toBeLessThanOrEqual(14);

  // let the 300 ms tap window of the first burst close before the next one
  await page.waitForTimeout(500);
  const beforeLeft = await currentTime(page);
  await doubleTap(page, "left");
  await expect
    .poll(async () => (await currentTime(page)) - beforeLeft, { timeout: 5_000 })
    .toBeLessThanOrEqual(-6);
  expect((await currentTime(page)) - beforeLeft).toBeGreaterThanOrEqual(-12);

  await page.getByRole("button", { name: "Avsluta filmen" }).click();
  await expect(page.locator(".grid-view")).toBeVisible();
  await expect(video(page)).toHaveCount(0);
  await expect(tile(page, KEPT)).toBeVisible();

  // 4. settings -------------------------------------------------------------
  await page.getByRole("button", { name: "Inställningar" }).click();
  for (const name of [
    "Butiksnamn",
    "Tillverkare och fyrverkerier",
    "Egna fyrverkerier",
    "Lagring",
    "Wi‑Fi",
    "Diagnostik",
    "Säkerhetskopia",
    "Installationsguide",
  ]) {
    await expect(page.getByRole("tab", { name })).toBeVisible();
  }
  await page.getByRole("button", { name: "Tillbaka" }).click();
  await expect(page.locator(".grid-view")).toBeVisible();

  // 5. TV mode --------------------------------------------------------------
  expect(await tvConnected(page)).toBe(false);
  writeFileSync(fakeTv, "");
  await expect.poll(() => tvConnected(page), { timeout: 15_000 }).toBe(true);
  // the page learns about it through the event stream
  await expect(page.locator(".tv-pill.on")).toBeVisible({ timeout: 15_000 });

  await tile(page, KEPT).click();
  const popup = page.getByRole("dialog", { name: KEPT });
  await expect(popup).toBeVisible();
  await expect(popup.getByText("Spelas på TV:n")).toBeVisible();
  await expect(popup.getByRole("button", { name: "Pausa" })).toBeVisible();
  await expect(popup.getByRole("button", { name: "Spola framåt 10 sekunder" })).toBeVisible();
  await expect(popup.getByRole("button", { name: "Spola tillbaka 10 sekunder" })).toBeVisible();
  await expect(page.locator("video")).toHaveCount(0);

  await popup.getByRole("button", { name: "Avsluta filmen" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page).not.toHaveURL(/#\/play/);
  await expect(tile(page, KEPT)).toBeVisible();
  await expect(page.locator("video")).toHaveCount(0);

  unlinkSync(fakeTv);
  await expect.poll(() => tvConnected(page), { timeout: 15_000 }).toBe(false);

  // 6. nothing went wrong in the page --------------------------------------
  expect(problems, problems.join("\n")).toEqual([]);
});

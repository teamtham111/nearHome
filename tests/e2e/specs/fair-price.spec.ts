import { test, expect } from "@playwright/test";

test.describe("Production fair-price model", () => {
  test("three real HDB addresses return CatBoost estimates with evidence", async ({ page }) => {
    test.setTimeout(180_000);
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await page.getByRole("button", { name: /save profile/i }).click();

    for (const [address, price, area] of [
      ["123 Bishan St 12", "680000", "91"],
      ["125 Bishan St 12", "680000", "91"],
      ["745 Pasir Ris Street 71", "738000", "127"],
    ]) {
      await page.getByLabel(/address/i).fill(address);
      await page.getByLabel(/asking price/i).fill(price);
      await page.getByLabel(/floor area/i).fill(area);
      await page.getByRole("button", { name: /add listing/i }).click();
    }

    await page.getByRole("button", { name: /run enrichment/i }).click();
    await expect(page.getByText(/Fair-price estimate/)).toBeVisible({ timeout: 120000 });
    await expect(page.getByText(/See valuation evidence/)).toHaveCount(3, { timeout: 120000 });

    // Model metadata is intentionally hidden from ordinary users. Verify the
    // production response directly instead of asserting a developer diagnostic
    // string in the UI.
    const sessionId = page.url().split("/").pop();
    const comparisonResponse = await page.request.get(`http://127.0.0.1:8000/api/v1/sessions/${sessionId}/comparison`);
    expect(comparisonResponse.ok()).toBeTruthy();
    const comparison = await comparisonResponse.json();
    const fairPrices = Object.values(comparison.fair_price_by_listing as Record<string, { method?: string; model_version?: string }>);
    expect(fairPrices).toHaveLength(3);
    expect(fairPrices.every((value) => value.method === "CATBOOST" && value.model_version === "catboost_v1")).toBeTruthy();

    // Regression: adding a flat after a completed run must not let stale
    // successful rows make the next enrichment click finish immediately.
    await page.getByLabel(/address/i).fill("201 Tampines St 21");
    await page.getByLabel(/asking price/i).fill("650000");
    await page.getByLabel(/floor area/i).fill("91");
    await page.getByRole("button", { name: /add listing/i }).click();
    await expect(page.getByText(/Your shortlist \(4\/5\)/)).toBeVisible();
    await page.getByRole("button", { name: /run enrichment/i }).click();
    await expect.poll(async () => {
      const response = await page.request.get(`http://127.0.0.1:8000/api/v1/sessions/${sessionId}/comparison`);
      if (!response.ok()) return 0;
      const refreshed = await response.json();
      return Object.keys(refreshed.fair_price_by_listing ?? {}).length;
    }, { timeout: 120000 }).toBe(4);
  });
});

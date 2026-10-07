const { chromium } = require("@playwright/test");
const fs = require("fs");
const path = require("path");

const outputDirectory = path.resolve(__dirname, "../../apps/web/public/paste-guide");
const howItWorksDirectory = path.resolve(__dirname, "../../apps/web/public/how-it-works");
const nearHomeUrl = process.env.NEARHOME_CAPTURE_URL || "http://localhost:3000";
const listingPhoto =
  "https://upload.wikimedia.org/wikipedia/commons/thumb/3/31/Bishan_HDB_apartment_block_501.jpg/1280px-Bishan_HDB_apartment_block_501.jpg";

const sampleListing = String.raw`<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <style>
      * { box-sizing: border-box; }
      ::selection { color: #102a43; background: #9ec5ff; }
      body { margin: 0; background: #f4f6f8; color: #172b4d; font: 16px/1.45 Arial, sans-serif; }
      header { height: 74px; display: flex; align-items: center; justify-content: space-between; padding: 0 54px; background: white; border-bottom: 1px solid #d9e2ec; }
      .brand { font-size: 22px; font-weight: 800; color: #0f766e; }
      nav { display: flex; gap: 30px; color: #52606d; font-size: 14px; }
      main { max-width: 1100px; margin: 28px auto; }
      .crumbs { color: #64748b; font-size: 13px; margin-bottom: 16px; }
      .listing { display: grid; grid-template-columns: 54% 46%; overflow: hidden; border: 1px solid #d9e2ec; border-radius: 14px; background: white; box-shadow: 0 10px 28px rgba(15, 23, 42, .08); }
      .photo { min-height: 355px; background: #dbe4ee center/cover no-repeat; }
      .details { padding: 30px 34px; }
      .eyebrow { display: inline-block; border-radius: 999px; background: #e6fffa; color: #0f766e; padding: 5px 11px; font-size: 12px; font-weight: 700; }
      h1 { margin: 16px 0 5px; font-size: 28px; line-height: 1.2; }
      .address { margin: 0; color: #52606d; }
      .price { margin: 24px 0 4px; font-size: 30px; font-weight: 800; color: #102a43; }
      .psf { color: #64748b; font-size: 14px; }
      .facts { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin: 24px 0; padding: 20px 0; border-block: 1px solid #e4e7eb; }
      .fact strong { display: block; font-size: 18px; }
      .fact span { color: #64748b; font-size: 12px; }
      .description { color: #475569; font-size: 14px; }
      .lower { display: grid; grid-template-columns: 2fr 1fr; gap: 20px; margin-top: 20px; }
      .card { min-height: 150px; padding: 20px; border: 1px solid #d9e2ec; border-radius: 12px; background: white; }
      .card h2 { margin: 0 0 10px; font-size: 18px; }
      .card p { margin: 5px 0; color: #52606d; font-size: 14px; }
    </style>
  </head>
  <body>
    <header>
      <div class="brand">Property listing</div>
      <nav><span>Buy</span><span>Rent</span><span>New projects</span><span>Guides</span></nav>
    </header>
    <main>
      <div class="crumbs">Home / HDB flats for sale / Bishan / Sample listing</div>
      <article class="listing">
        <div class="photo" style="background-image:url('${listingPhoto}')" role="img" aria-label="HDB apartment block in Bishan"></div>
        <div class="details">
          <span class="eyebrow">Example HDB listing</span>
          <h1>501 Bishan Street 11</h1>
          <p class="address">Bishan, Singapore 570501</p>
          <p class="price">S$728,000</p>
          <p class="psf">S$736 psf</p>
          <div class="facts">
            <div class="fact"><strong>3</strong><span>Bedrooms</span></div>
            <div class="fact"><strong>2</strong><span>Bathrooms</span></div>
            <div class="fact"><strong>990 sqft</strong><span>Floor area</span></div>
          </div>
          <p class="description">Sample listing content showing the page details NearHome can extract after you select and copy the complete page.</p>
        </div>
      </article>
      <section class="lower">
        <div class="card"><h2>About this property</h2><p>99-year leasehold HDB flat.</p><p>Near Bishan MRT and neighbourhood shops.</p></div>
        <div class="card"><h2>Key details</h2><p>Property type: HDB</p><p>Furnishing: Partial</p></div>
      </section>
    </main>
  </body>
</html>`;

async function selectPage(page) {
  await page.evaluate(() => {
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(document.body);
    selection.removeAllRanges();
    selection.addRange(range);
  });
}

(async () => {
  fs.mkdirSync(outputDirectory, { recursive: true });
  fs.mkdirSync(howItWorksDirectory, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  const sourcePage = await browser.newPage({ viewport: { width: 1200, height: 675 }, deviceScaleFactor: 1 });
  await sourcePage.setContent(sampleListing, { waitUntil: "networkidle" });
  await sourcePage.locator(".photo").waitFor({ state: "visible" });
  await sourcePage.screenshot({ path: path.join(outputDirectory, "open-page.jpg"), type: "jpeg", quality: 86 });
  await selectPage(sourcePage);
  await sourcePage.screenshot({ path: path.join(outputDirectory, "select-page.jpg"), type: "jpeg", quality: 86 });

  await sourcePage.evaluate(() => window.scrollTo(0, 185));
  await selectPage(sourcePage);
  await sourcePage.screenshot({ path: path.join(outputDirectory, "copy-page.jpg"), type: "jpeg", quality: 86 });

  const nearHome = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
  const guideSessionId = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
  let profileSaved = false;
  await nearHome.route("**/api/v1/sessions", async (route) => {
    if (route.request().method() !== "POST") return route.fallback();
    await route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({ session_id: guideSessionId }),
    });
  });
  await nearHome.route(`**/api/v1/sessions/${guideSessionId}**`, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/buyer-profile") && route.request().method() === "PUT") {
      profileSaved = true;
      await route.fulfill({ status: 200, contentType: "application/json", body: "{}" });
      return;
    }
    if (pathname.endsWith("/smart-paste") && route.request().method() === "POST") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          listing_input_id: "paste-guide-listing",
          suggested_values: {
            address: "501 Bishan Street 11, Singapore 570501",
            asking_price: 728000,
            floor_area_sqm: 92,
            flat_type: "4 ROOM",
            listing_flat_subtype: "4A",
            flat_model: "Model A",
            remaining_lease_years: 72,
          },
          evidence_by_field: {},
          field_sources: {
            address: "listing_text",
            asking_price: "listing_text",
            floor_area_sqm: "listing_text",
            flat_type: "listing_text",
          },
          candidates: {},
          extraction_warnings: [],
          agent_claims: [],
          sourceType: "text",
          sourceUrl: null,
        }),
      });
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        session_id: guideSessionId,
        demo_mode: false,
        profile_saved: profileSaved,
        buyer_profile: profileSaved
          ? {
              max_budget: 730000,
              main_transport_mode: "MAINLY_PUBLIC_TRANSPORT",
              schools_matter: false,
              named_schools: [],
              named_school: null,
              priorities: ["AFFORDABILITY"],
              important_locations: [],
            }
          : null,
        listings: [],
        listing_count: 0,
      }),
    });
  });
  await nearHome.goto(nearHomeUrl, { waitUntil: "networkidle" });
  await nearHome.getByRole("button", { name: /start new comparison/i }).click();
  const profile = nearHome.locator("#buyer-profile");
  await profile.scrollIntoViewIfNeeded();
  const profileBox = await profile.boundingBox();
  if (!profileBox) throw new Error("Buyer profile was not visible");
  await nearHome.screenshot({
    path: path.join(howItWorksDirectory, "profile-nearhome.jpg"),
    type: "jpeg",
    quality: 88,
    clip: { x: Math.max(0, profileBox.x - 20), y: Math.max(0, profileBox.y - 16), width: Math.min(1120, profileBox.width + 40), height: 830 },
  });
  await nearHome.getByRole("button", { name: /save profile/i }).click();
  const textarea = nearHome.getByRole("textbox", { name: "Listing text" });
  await textarea.fill(
    "Buy  Rent  New launches  Find an agent\n\n501 BISHAN STREET 11  501 Bishan Street 11\nFOR SALE  S$728,000   S$736 psf\nEst. mortgage S$2,841/mo | 4A / MODEL A\n3 beds  2 baths  990 sqft  92 sqm\nListed today  Share  Save  Contact agent\n\nAbout this property  SPACIOUS! NEAR MRT!\n501 Bishan Street 11 · 99-year leasehold\nSchools nearby  Amenities  Similar listings",
  );
  await textarea.focus();
  const panel = nearHome.locator("#smart-paste-panel");
  await panel.scrollIntoViewIfNeeded();
  const box = await panel.boundingBox();
  if (!box) throw new Error("Smart Paste panel was not visible");
  await nearHome.screenshot({
    path: path.join(outputDirectory, "paste-nearhome.jpg"),
    type: "jpeg",
    quality: 88,
    clip: { x: Math.max(0, box.x - 20), y: Math.max(0, box.y - 16), width: 820, height: 470 },
  });

  await nearHome.getByRole("button", { name: /^add a flat$/i }).click();
  await nearHome.getByText(/review extracted fields/i).waitFor();
  const reviewForm = nearHome.locator("#manual-listing-form");
  await reviewForm.scrollIntoViewIfNeeded();
  const reviewBox = await reviewForm.boundingBox();
  if (!reviewBox) throw new Error("Formatted listing review was not visible");
  await nearHome.screenshot({
    path: path.join(outputDirectory, "review-nearhome.jpg"),
    type: "jpeg",
    quality: 88,
    clip: { x: Math.max(0, reviewBox.x - 20), y: Math.max(0, reviewBox.y - 16), width: 820, height: 470 },
  });

  const comparisonPage = await browser.newPage({ viewport: { width: 1400, height: 900 }, deviceScaleFactor: 1 });
  const comparisonSessionId = "how-it-works-comparison";
  const comparisonListings = [
    { listing_id: "bishan", display_name: "501 Bishan Street 11", address: "501 Bishan Street 11", asking_price: 728000, floor_area_sqm: 92, flat_type: "4 ROOM", flat_model: "Model A", storey_range: "07 TO 09", remaining_lease_months: 864 },
    { listing_id: "tampines", display_name: "220 Tampines Street 23", address: "220 Tampines Street 23", asking_price: 698000, floor_area_sqm: 95, flat_type: "4 ROOM", flat_model: "Model A", storey_range: "10 TO 12", remaining_lease_months: 816 },
  ];
  await comparisonPage.route(`**/api/v1/sessions/${comparisonSessionId}**`, async (route) => {
    const pathname = new URL(route.request().url()).pathname;
    if (pathname.endsWith("/comparison")) {
      const values = {
        bishan: { asking_price: 728000, budget_difference: 22000, floor_area_sqm: 92, price_per_sqm: 7913, remaining_lease_years: 72 },
        tampines: { asking_price: 698000, budget_difference: 52000, floor_area_sqm: 95, price_per_sqm: 7347, remaining_lease_years: 68 },
      };
      const immediateMetrics = Object.entries(values).flatMap(([listingId, metrics]) => Object.entries(metrics).map(([metricName, rawValue]) => ({ listing_id: listingId, metric_name: metricName, raw_value: rawValue, unit: metricName.includes("price") || metricName === "budget_difference" ? "SGD" : null, status: "AVAILABLE", explanation: "Confirmed listing detail" })));
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
        session_id: comparisonSessionId,
        listing_count: 2,
        can_compare: true,
        immediate_metrics: immediateMetrics,
        requirement_results: [],
        preference_scores: [],
        recommendation: { recommended_listing_id: "bishan", is_tie: false, is_provisional: false, one_sentence_summary: "Bishan leads for your selected journey and transport priorities, while Tampines has the lower asking price and more floor area.", confidence: "MEDIUM", confidence_reasons: ["Most selected factors have available evidence"], why_not_selected: { tampines: "Lower asking price, but longer regular journeys for this profile." }, decision_hinge: "Journey convenience versus price" },
        fair_price_status: "NOT_STARTED",
        fair_price_by_listing: {},
        transport_by_listing: {},
        driving_by_listing: {},
        schools_by_listing: {},
        observations: [],
        journey_results: [],
        regular_destination_journeys: [],
        enrichment_summary: [],
        demo_mode: false,
      }) });
      return;
    }
    await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({
      session_id: comparisonSessionId,
      demo_mode: false,
      profile_saved: true,
      buyer_profile: { max_budget: 750000, main_transport_mode: "BOTH", schools_matter: false, named_schools: [], named_school: null, priorities: ["AFFORDABILITY", "PUBLIC_TRANSPORT"], important_locations: [] },
      listings: comparisonListings,
      listing_count: 2,
    }) });
  });
  await comparisonPage.goto(`${nearHomeUrl}/session/${comparisonSessionId}/comparison`, { waitUntil: "networkidle" });
  await comparisonPage.getByRole("heading", { name: "Your comparison is ready", level: 2 }).waitFor();
  const comparison = comparisonPage.locator("main");
  const comparisonBox = await comparison.boundingBox();
  if (!comparisonBox) throw new Error("Comparison result was not visible");
  await comparisonPage.screenshot({
    path: path.join(howItWorksDirectory, "comparison-nearhome.jpg"),
    type: "jpeg",
    quality: 88,
    clip: { x: Math.max(0, comparisonBox.x), y: Math.max(0, comparisonBox.y + 160), width: Math.min(1200, comparisonBox.width), height: 675 },
  });

  await browser.close();
})();

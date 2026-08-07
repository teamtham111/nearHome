import { test, expect } from "@playwright/test";

test.describe("NearHome manual comparison (no external API)", () => {
  test("buyer profile accepts multiple named schools", async ({ page }) => {
    await page.route("**/api/geocode", async (route) => {
      const query = JSON.parse(route.request().postData() ?? "{}").query as string;
      const raffles = /raffles/i.test(query);
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          suggestions: [
            raffles
              ? { place_id: "school-raffles", description: "1 Raffles Institution Lane, Singapore", main_text: "Raffles Institution", formatted_address: "1 Raffles Institution Lane, Singapore", latitude: 1.345, longitude: 103.84 }
              : { place_id: "school-nanyang", description: "2 Nanyang Avenue, Singapore", main_text: "Nanyang Primary School", formatted_address: "2 Nanyang Avenue, Singapore", latitude: 1.349, longitude: 103.68 },
          ],
        }),
      });
    });
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();

    await page.getByRole("checkbox", { name: /schools matter/i }).check();
    await page.getByLabel("Search for a named school").fill("Raffles Institution");
    await page.getByRole("list", { name: "Named school search results" }).getByRole("button").click();
    await page.getByLabel("Search for a named school").fill("Nanyang Primary School");
    await page.getByRole("list", { name: "Named school search results" }).getByRole("button").click();
    await page.getByRole("button", { name: /save profile/i }).click();

    await expect(page.getByText("Profile saved")).toBeVisible();
    await expect(page.getByRole("list", { name: "Selected named schools" })).toContainText("Raffles Institution");
    await expect(page.getByRole("list", { name: "Selected named schools" })).toContainText("Nanyang Primary School");
  });

  test("buyer profile rejects an unconfirmed named-school query", async ({ page }) => {
    await page.route("**/api/geocode", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ suggestions: [] }) });
    });
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await page.getByRole("checkbox", { name: /schools matter/i }).check();
    await page.getByLabel("Search for a named school").fill("Made Up Singapore School");
    await expect(page.getByText(/No matching Singapore school was found/)).toBeVisible();
    await page.getByRole("button", { name: /save profile/i }).click();
    await expect(page.getByText(/Choose each named school from the Singapore address results/)).toBeVisible();
  });

  test("rehydrates a saved important location without losing it on update", async ({ page }) => {
    await page.route("**/api/v1/sessions/location-test", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "location-test",
          demo_mode: true,
          profile_saved: true,
          buyer_profile: {
            max_budget: 730000,
            main_transport_mode: "MAINLY_PUBLIC_TRANSPORT",
            schools_matter: false,
            named_schools: [],
            named_school: null,
            priorities: ["AFFORDABILITY"],
            important_locations: [
              {
                important_location_id: "location-1",
                label: "Work",
                place_id: "work-place",
                formatted_address: "1 Raffles Place, Singapore",
                latitude: 1.2847,
                longitude: 103.8511,
                usual_day_type: "WEEKDAY",
                departure_time_local: "08:30:00",
                transport_mode: "PUBLIC_TRANSPORT",
                is_complete: true,
              },
            ],
          },
          listings: [],
          listing_count: 0,
        }),
      });
    });

    await page.goto("/session/location-test");
    await expect(page.getByText("1 Raffles Place, Singapore")).toBeVisible();
    await expect(page.getByText(/Weekdays at 8:30 AM/)).toBeVisible();
    await page.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByPlaceholder("e.g. Work")).toHaveValue("Work");
    await expect(page.getByPlaceholder("Search address…")).toHaveValue("1 Raffles Place, Singapore");
    await expect(page.locator('input[type="time"]')).toHaveValue("08:30");
    await expect(page.getByText("Confirmed: 1 Raffles Place, Singapore")).toBeVisible();
  });

  test("uses segmented controls and a ranked priority builder", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();

    const transport = page.getByRole("group", { name: "Main transport mode" });
    await transport.getByRole("button", { name: "Driving" }).click();
    await expect(transport.getByRole("button", { name: /Driving/ })).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByLabel("Priority 1 factor")).toHaveValue("AFFORDABILITY");

    await page.getByRole("button", { name: /Add a priority/ }).click();
    await page.getByRole("button", { name: "Choose a priority to add" }).click();
    await page.getByRole("listbox", { name: "Available priorities" }).getByRole("option", { name: "Public transport" }).click();
    await expect(page.getByRole("list", { name: "Decision priorities" }).getByRole("listitem")).toHaveCount(2);
    const transportDragHandle = page.getByRole("button", { name: "Drag to reorder Public transport" });
    await transportDragHandle.focus();
    await transportDragHandle.press("Enter");
    await transportDragHandle.press("ArrowUp");
    await transportDragHandle.press("Enter");
    await expect(page.getByLabel("Priority 1 factor")).toHaveValue("PUBLIC_TRANSPORT");
    await page.getByRole("button", { name: /Remove Public transport/ }).click();
    await expect(page.getByLabel("Priority 1 factor")).toHaveValue("AFFORDABILITY");
  });

  test("collapses, edits and removes a confirmed regular destination", async ({ page }) => {
    await page.route("**/api/geocode", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ suggestions: [{ place_id: "work-place", description: "1 Raffles Place, Singapore", main_text: "Raffles Place", formatted_address: "1 Raffles Place, Singapore", latitude: 1.2847, longitude: 103.8511 }] }),
      });
    });
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await page.getByRole("button", { name: /add regular destination/i }).click();
    await page.getByPlaceholder("Search address…").fill("Raffles Place");
    await page.getByRole("button", { name: "1 Raffles Place, Singapore" }).click();
    await page.getByRole("button", { name: "Done" }).click();
    await expect(page.getByText("1 Raffles Place, Singapore")).toBeVisible();
    await page.getByRole("button", { name: "Edit" }).click();
    await expect(page.getByPlaceholder("Search address…")).toHaveValue("1 Raffles Place, Singapore");
    await page.getByRole("button", { name: "Remove destination" }).click();
    await page.getByRole("dialog").getByRole("button", { name: "Remove destination" }).click();
    await expect(page.getByRole("button", { name: /add regular destination/i })).toBeVisible();
  });

  test("removes a flat by stable ID through confirmation and handles small shortlists", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await page.getByRole("button", { name: /save profile/i }).click();

    for (const address of ["123 Bishan St 12", "201 Tampines St 21", "217 Bishan St 23"]) {
      await page.getByLabel(/address/i).fill(address);
      await page.getByLabel(/asking price/i).fill("650000");
      await page.getByLabel(/floor area/i).fill("91");
      await page.getByRole("button", { name: /add listing/i }).click();
    }

    const middleRemove = page.getByRole("button", { name: /remove flat 201 tampines st 21/i });
    await middleRemove.click();
    await expect(page.getByRole("heading", { name: "Remove this flat?" })).toBeVisible();
    await expect(page.getByText(/Remove 201 Tampines St 21 from your shortlist\?/)).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByRole("heading", { name: "Remove this flat?" })).toBeHidden();
    await expect(middleRemove).toBeFocused();

    await middleRemove.click();
    await page.getByRole("button", { name: "Remove flat", exact: true }).click();
    await expect(page.getByText("Your shortlist (2/5)")).toBeVisible();
    await expect(page.getByText("201 Tampines St 21")).toHaveCount(0);
    await expect(page.getByText("Add another flat to compare this listing.")).toHaveCount(0);

    await page.getByRole("button", { name: /remove flat 123 bishan st 12/i }).click();
    await page.getByRole("button", { name: "Remove flat", exact: true }).click();
    await expect(page.getByText("Your shortlist (1/5)")).toBeVisible();
    await expect(page.getByText("Add another flat to compare this listing.")).toBeVisible();

    await page.getByRole("button", { name: /remove flat 217 bishan st 23/i }).click();
    await page.getByRole("button", { name: "Remove flat", exact: true }).click();
    await expect(page.getByRole("heading", { name: "Add a flat" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Manual entry" })).toBeVisible();
    await expect(page.getByRole("tab", { name: "Smart Paste" })).toBeVisible();
  });

  test("restores the flat when server-side removal fails", async ({ page }) => {
    await page.route("**/api/v1/sessions/*/listings/*", async (route) => {
      if (route.request().method() === "DELETE") {
        await route.fulfill({
          status: 500,
          contentType: "application/json",
          body: JSON.stringify({ detail: "Could not remove this flat" }),
        });
      } else {
        await route.continue();
      }
    });

    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await page.getByRole("button", { name: /save profile/i }).click();
    await page.getByLabel(/address/i).fill("123 Bishan St 12");
    await page.getByLabel(/asking price/i).fill("650000");
    await page.getByLabel(/floor area/i).fill("91");
    await page.getByRole("button", { name: /add listing/i }).click();

    await page.getByRole("button", { name: /remove flat 123 bishan st 12/i }).click();
    await page.getByRole("button", { name: "Remove flat", exact: true }).click();
    await expect(page.getByRole("alert").filter({ hasText: "Could not remove this flat" })).toBeVisible();
    await expect(page.getByText("123 Bishan St 12", { exact: true }).first()).toBeVisible();
  });

  test("fair-price display translates evidence without raw diagnostics", async ({ page }) => {
    const listing = {
      listing_id: "listing-1",
      display_name: "217 Bishan Street 23",
      address: "217 Bishan Street 23",
      asking_price: 850000,
      floor_area_sqm: 103,
      flat_type: "4 ROOM",
      flat_model: null,
      storey_range: null,
      remaining_lease_months: 777,
    };
    const comparables = Array.from({ length: 6 }, (_, index) => ({
      transaction_id: `transaction-${index}`,
      transaction_date: `2026-0${Math.min(index + 1, 9)}`,
      block: String(217 + index),
      street: "Bishan Street 23",
      flat_type: "4 ROOM",
      flat_model: "Model A",
      floor_area_sqm: 102 + (index % 2),
      remaining_lease_months: 777,
      resale_price: 790000 + index * 5000,
      age_months: index + 1,
      similarity: 0.21 - index * 0.01,
    }));
    const fairPrice = {
      central_estimate: 838252,
      range_low: 790145,
      range_high: 908773,
      asking_difference_dollars: 11748,
      asking_difference_pct: 1.4,
      value_gap_percentage: -1.4,
      confidence: "MEDIUM",
      confidence_reasons: ["Comparable price spread is wide"],
      comparables,
      all_comparables: comparables,
      method: "CATBOOST",
      model_version: "catboost_v1",
      comparable_model_version: "weighted_comparables_v3",
      comparable_evidence: {
        eligible_comparable_count: 209,
        effective_weighted_count: 7.6,
        average_similarity: 0.07692,
        comparable_price_spread: 300000,
        filter_status: { storey_range: { status: "omitted_missing" } },
      },
      filter_status: {
        town: { status: "applied" },
        flat_type: { status: "applied" },
        flat_model: { status: "omitted_missing" },
        area_band: { status: "applied" },
        lease_band: { status: "applied" },
        storey_range: { status: "omitted_missing" },
        relaxation_steps: [],
      },
      town: "BISHAN",
      remaining_lease_source: "hdb_same_block_transactions",
      remaining_lease_as_of_date: "2026-08-04",
      remaining_lease_estimate: {
        display_value: "Estimated remaining lease: About 64 years 9 months",
        lease_commencement_year: 1992,
        source: "hdb_same_block_transactions",
        confidence: "high",
        is_estimated: true,
        as_of_date: "2026-08-04",
      },
      status: "AVAILABLE",
    };
    const transportComponent = (name: string, score: number) => ({
      name,
      value: {},
      score,
      weight: 0.25,
      status: "calculated",
      explanation: "Confirmed transport evidence.",
      strengths: [],
      limitations: [],
      evidence: [],
      source: "LTA_REFERENCE",
      provenance: "REFERENCE",
      confidence: "high",
    });
    const completeTransport = {
      overall_score: 84.2, display_score: 84.2, unrounded_score: 84.2, is_complete: true, counts_toward_recommendation: true, coverage_ratio: 1, assessed_components: [], excluded_components: [], warnings: [],
      components: [transportComponent("access", 80), transportComponent("bus_coverage", 85), transportComponent("mrt_reach", 86), transportComponent("route_resilience", 84)],
    };
    const partialTransport = {
      ...completeTransport,
      overall_score: 72.4,
      display_score: 72.4,
      unrounded_score: 72.4,
      is_complete: false,
      coverage_ratio: 0.75,
      excluded_components: ["route_resilience"],
      components: completeTransport.components.slice(0, 3),
    };

    await page.route("**/api/v1/sessions/ui-test", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify({
            session_id: "ui-test",
            demo_mode: false,
            profile_saved: true,
            buyer_profile: {
              max_budget: 900000,
              main_transport_mode: "MAINLY_PUBLIC_TRANSPORT",
              schools_matter: false,
              named_schools: [],
              named_school: null,
              priorities: ["FAIR_PRICE"],
            },
            listings: [listing, { ...listing, listing_id: "listing-2", display_name: "220 Bishan Street 23", asking_price: 800000 }],
            listing_count: 2,
          }),
        });
      }
    });
    await page.route("**/api/v1/sessions/ui-test/comparison", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "ui-test",
          listing_count: 2,
          can_compare: true,
          immediate_metrics: [
            { listing_id: "listing-1", metric_name: "asking_price", raw_value: 850000, unit: "SGD", status: "AVAILABLE", explanation: "" },
            { listing_id: "listing-2", metric_name: "asking_price", raw_value: 850000, unit: "SGD", status: "AVAILABLE", explanation: "" },
          ],
          requirement_results: [],
          preference_scores: [],
          recommendation: null,
          fair_price_status: "AVAILABLE",
          fair_price_by_listing: { "listing-1": fairPrice, "listing-2": fairPrice },
          transport_by_listing: { "listing-1": completeTransport, "listing-2": partialTransport },
          driving_by_listing: {},
          schools_by_listing: {},
          observations: [],
          journey_results: [],
          enrichment_summary: [],
          demo_mode: false,
        }),
      });
    });

    await page.goto("/session/ui-test/comparison");
    await expect(page.getByRole("navigation", { name: "Comparison progress" })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Which flat fits best?" })).toBeVisible();
    await expect(page.getByText("Overall fit", { exact: true })).toHaveCount(0);
    await expect(page.getByText("Estimated value: S$838,252").first()).toBeVisible();
    await expect(page.getByText("S$11,748 above estimate · 1.4%").first()).toBeVisible();
    await expect(page.getByText("Close to estimated market value").first()).toBeVisible();
    await expect(page.getByText("S$38,252 below estimate · 4.6%").first()).toBeVisible();
    await expect(page.getByText("Medium confidence").first()).toBeVisible();
    await expect(page.getByText("84/100 · Good").first()).toBeVisible();
    await expect(page.getByText("72/100 · Good").first()).toBeVisible();
    await expect(page.getByText("Partial result").first()).toBeVisible();
    await expect(page.getByText("Estimate unavailable")).toHaveCount(0);
    await expect(page.getByText("catboost_v1")).toHaveCount(0);
    await expect(page.getByText("weighted_comparables_v3")).toHaveCount(0);
    await expect(page.getByText(/effective weighted|similarity 0\.07692|View raw evidence/i)).toHaveCount(0);

    await page.getByText("See valuation evidence").first().click();
    await expect(page.getByText("Why this estimate?").first()).toBeVisible();
    await expect(page.getByText("Similar recent transactions").first()).toBeVisible();
    await expect(page.getByText(/6 closest matches shown from 209 eligible records/).first()).toBeVisible();
    await expect(page.getByText("Very similar").first()).toBeVisible();
    await expect(page.getByText(/View all comparable transactions|View all transactions/i)).toHaveCount(0);

    await expect(page.getByText("Transport breakdown").first()).toBeVisible();
    await expect(page.getByText("View transport details")).toHaveCount(0);
    const accessButton = page.getByRole("button", { name: "Access details" }).first();
    const busButton = page.getByRole("button", { name: "Bus coverage details" }).first();
    await expect(accessButton).toHaveAttribute("aria-expanded", "false");
    await expect(busButton).toHaveAttribute("aria-expanded", "false");
    await accessButton.press("Enter");
    await expect(accessButton).toHaveAttribute("aria-expanded", "true");
    await expect(busButton).toHaveAttribute("aria-expanded", "false");
    await expect(accessButton.locator("svg")).toHaveClass(/rotate-180/);
    await expect(page.getByText("Best network-entry option").first()).toBeVisible();
    await expect(page.getByText("Main trade-off")).toHaveCount(0);
    await expect(page.getByText("Best for", { exact: true })).toHaveCount(0);
    await expect(page.getByText("How this was calculated")).toHaveCount(0);
  });

  test("shows compact nearby-school evidence with a clear winner and shared unmatched warning", async ({ page }) => {
    await page.route("**/api/v1/sessions/schools-ui", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "schools-ui", demo_mode: false, profile_saved: true,
          buyer_profile: { max_budget: 900000, main_transport_mode: "MAINLY_PUBLIC_TRANSPORT", schools_matter: true, named_schools: ["Made Up School"], priorities: ["SCHOOLS"] },
          listings: [
            { listing_id: "limited", display_name: "54 New Upper Changi Road", address: "54 New Upper Changi Road", asking_price: 700000, floor_area_sqm: 90, flat_type: "4 ROOM" },
            { listing_id: "strong", display_name: "217 Bishan Street 23", address: "217 Bishan Street 23", asking_price: 800000, floor_area_sqm: 90, flat_type: "4 ROOM" },
            { listing_id: "unavailable", display_name: "Unknown coordinates", address: "Unknown coordinates", asking_price: 750000, floor_area_sqm: 90, flat_type: "4 ROOM" },
          ], listing_count: 3,
        }),
      });
    });
    await page.route("**/api/v1/sessions/schools-ui/comparison", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "schools-ui", listing_count: 3, can_compare: true,
          immediate_metrics: [
            { listing_id: "limited", metric_name: "asking_price", raw_value: 700000, unit: "SGD", status: "AVAILABLE", explanation: "" },
            { listing_id: "strong", metric_name: "asking_price", raw_value: 800000, unit: "SGD", status: "AVAILABLE", explanation: "" },
            { listing_id: "unavailable", metric_name: "asking_price", raw_value: 750000, unit: "SGD", status: "AVAILABLE", explanation: "" },
          ], requirement_results: [], preference_scores: [], recommendation: null,
          fair_price_status: "NOT_STARTED", fair_price_by_listing: {}, transport_by_listing: {}, driving_by_listing: {},
          schools_by_listing: {
            limited: { score: 0, score_status: "calculated", schools_within_1km: 0, schools_within_2km: 0, nearest_school_distance_km: null, nearby_schools: [], named_school_distances_km: { "Made Up School": null }, matched_named_schools: { "Made Up School": null } },
            strong: { score: 30, score_status: "calculated", schools_within_1km: 2, schools_within_2km: 2, nearest_school_distance_km: 0.569615893154275, nearby_schools: [{ school_name: "Catholic High School", level: "PRIMARY", distance_km: 0.569615893154275, address: "Singapore" }, { school_name: "Kuo Chuan Presbyterian Primary School", level: "PRIMARY", distance_km: 0.7, address: "Singapore" }], named_school_distances_km: { "Made Up School": null }, matched_named_schools: { "Made Up School": null } },
            unavailable: { score: null, score_status: "missing_input", missing_reasons: ["Coordinates unavailable"], nearby_schools: [] },
          },
          observations: [], journey_results: [], enrichment_summary: [], demo_mode: false,
        }),
      });
    });

    await page.goto("/session/schools-ui/comparison");
    await expect(page.getByText("Better for nearby schools: 217 Bishan Street 23")).toBeVisible();
    await expect(page.getByText("Strong school access")).toBeVisible();
    await expect(page.getByText("No nearby schools found")).toBeVisible();
    await expect(page.getByText("Not assessed", { exact: true })).toBeVisible();
    await expect(page.getByText("2 total", { exact: true })).toBeVisible();
    await expect(page.getByText("570 m", { exact: true })).toBeVisible();
    await expect(page.getByText("0.569615893154275 km")).toHaveCount(0);
    await expect(page.getByText(/We could not match 1 selected school/)).toHaveCount(1);

    const expand = page.getByRole("button", { name: "View 2 nearby schools" });
    await expect(expand).toHaveAttribute("aria-expanded", "false");
    await expand.click();
    await expect(page.getByRole("button", { name: "Hide 2 nearby schools" })).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText("Catholic High School")).toBeVisible();
    await expect(page.getByText("Kuo Chuan Presbyterian Primary School")).toBeVisible();
  });

  test("two-listing comparison flow", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await expect(page).toHaveURL(/\/session\//);

    // Save profile
    await page.getByRole("button", { name: /save profile/i }).click();

    // Add first listing
    await page.getByLabel(/address/i).fill("123 Bishan St 12");
    await page.getByLabel(/asking price/i).fill("650000");
    await page.getByLabel(/floor area/i).fill("91");
    await page.getByRole("button", { name: /add listing|confirm listing/i }).click();

    await expect(page.getByText(/1\/5 flats added/)).toBeVisible();

    // Add second listing
    await page.getByLabel(/address/i).fill("125 Bishan St 12");
    await page.getByLabel(/asking price/i).fill("680000");
    await page.getByRole("button", { name: /add listing|confirm listing/i }).click();

    await expect(page.getByText(/Price and affordability/)).toBeVisible();
    await expect(page.getByText(/Fair-price estimate/)).toBeVisible();
  });

  test("shows a recoverable warning instead of failing on a duplicate listing", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await page.getByRole("button", { name: /save profile/i }).click();

    const listing = {
      address: "123 Bishan St 12",
      askingPrice: "650000",
      floorArea: "91",
    };
    await page.getByLabel(/address/i).fill(listing.address);
    await page.getByLabel(/asking price/i).fill(listing.askingPrice);
    await page.getByLabel(/floor area/i).fill(listing.floorArea);
    await page.getByRole("button", { name: /add listing/i }).click();
    await expect(page.getByText("Your shortlist (1/5)")).toBeVisible();

    await page.getByLabel(/address/i).fill(listing.address);
    await page.getByLabel(/asking price/i).fill(listing.askingPrice);
    await page.getByLabel(/floor area/i).fill(listing.floorArea);
    await page.getByRole("button", { name: /add listing/i }).click();

    await expect(
      page.getByRole("alert").filter({ hasText: "already in your shortlist" }),
    ).toBeVisible();
    await expect(page.getByText("Your shortlist (1/5)")).toBeVisible();
  });

  test("keeps buyer profile and add-flat cards on one responsive workflow grid", async ({ page }) => {
    await page.route("**/api/v1/sessions", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ session_id: "layout-test" }) });
    });
    await page.route("**/api/v1/sessions/layout-test", async (route) => {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          session_id: "layout-test", demo_mode: true, profile_saved: false,
          buyer_profile: null, listings: [], listing_count: 0,
        }),
      });
    });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();

    const profile = page.locator("#buyer-profile");
    const addFlat = page.locator("section").filter({ has: page.getByRole("heading", { name: "Add a flat" }) });
    const profileBox = await profile.boundingBox();
    const addFlatBox = await addFlat.boundingBox();
    expect(profileBox).not.toBeNull();
    expect(addFlatBox).not.toBeNull();
    expect(Math.abs((profileBox?.x ?? 0) - (addFlatBox?.x ?? 0))).toBeLessThanOrEqual(1);
    expect(Math.abs((profileBox?.width ?? 0) - (addFlatBox?.width ?? 0))).toBeLessThanOrEqual(1);

    const budgetInput = page.getByLabel("Maximum purchase budget");
    await expect(profile.getByText("$", { exact: true })).toHaveCount(0);
    for (const value of ["0", "850000", "1000000"]) {
      await budgetInput.fill(value);
      await expect(budgetInput).toHaveValue(value);
    }

    const manualTab = page.getByRole("tab", { name: "Manual entry" });
    const smartPasteTab = page.getByRole("tab", { name: "Smart Paste" });
    await manualTab.focus();
    await manualTab.press("ArrowRight");
    await expect(smartPasteTab).toBeFocused();
    await expect(smartPasteTab).toHaveAttribute("aria-selected", "true");
    await smartPasteTab.press("ArrowLeft");
    await expect(manualTab).toBeFocused();

    for (const width of [1280, 1024, 768, 390]) {
      await page.setViewportSize({ width, height: 900 });
      await expect(profile).toBeVisible();
      await expect(addFlat).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    }
  });
});

test.describe("Smart Paste flow", () => {
  test("discards an extracted listing before confirmation", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();

    await page.getByRole("tab", { name: /smart paste/i }).click();
    await page
      .getByRole("textbox", { name: /paste any property listing url/i })
      .fill("123 Bishan Street 12, 4-room HDB flat, 91 sqm, asking S$650,000, remaining lease 65 years");
    await page.getByRole("button", { name: /^add a flat$/i }).click();

    await expect(page.getByText(/review extracted fields/i)).toBeVisible({ timeout: 15000 });
    await page.getByRole("button", { name: "Discard listing" }).click();

    await expect(page.getByRole("textbox", { name: /paste any property listing url/i })).toBeVisible();
    await expect(page.getByText(/review extracted fields/i)).toHaveCount(0);
    await expect(page.getByText("Your shortlist (1/5)")).toHaveCount(0);
  });

  test("extract and confirm listing", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();

    await page.getByRole("tab", { name: /smart paste/i }).click();
    await page
      .getByRole("textbox", { name: /paste any property listing url/i })
      .fill("123 Bishan Street 12, 4-room HDB flat, 91 sqm, asking S$650,000, remaining lease 65 years");
    await page.getByRole("button", { name: /^add a flat$/i }).click();

    await expect(page.getByText(/review extracted fields/i)).toBeVisible({ timeout: 15000 });
    await page.getByRole("button", { name: /confirm listing/i }).click();
  });
});

test.describe("Enrichment and fair price", () => {
  test("run enrichment shows fair-price data", async ({ page }) => {
    await page.route("**/api/v1/sessions/*/enrichment/start", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 500));
      await route.continue();
    });
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await page.getByRole("button", { name: /save profile/i }).click();

    for (const [addr, price] of [
      ["123 Bishan St 12", "650000"],
      ["201 Tampines St 21", "580000"],
    ]) {
      await page.getByLabel(/address/i).fill(addr);
      await page.getByLabel(/asking price/i).fill(price);
      await page.getByLabel(/floor area/i).fill("91");
      await page.getByRole("button", { name: /add listing/i }).click();
    }

    await page.getByRole("button", { name: /run enrichment/i }).click();
    await expect(page.getByRole("progressbar", { name: "Enrichment progress" })).toBeVisible();
    await expect(page.getByText(/Fair-price estimate/)).toBeVisible();
    await expect(page.getByText(/comparable|awaiting|insufficient/i).first()).toBeVisible({ timeout: 20000 });
    await expect(page.getByRole("heading", { name: "Public transport strength" })).toBeVisible();
  });
});

test.describe("Driving Connectivity presentation", () => {
  test("renders complete and provisional results for Pasir Ris and Yishun", async ({ page }) => {
    const component = (name: string, score: number | null, weight: number, value: unknown = {}, evidence: Array<Record<string, unknown>> = []) => ({
      name, value, score, weight, status: score == null ? "not_assessed" : "calculated", explanation: "Technical explanation", strengths: [], limitations: [], evidence, source: "test source", provenance: "ROUTED_LIVE", confidence: "high",
    });
    const completeDriving = {
      overall_score: 89.8, display_score: 89.8, unrounded_score: 89.8, is_complete: true, counts_toward_recommendation: true, coverage_ratio: 1, assessed_components: [], excluded_components: [], warnings: [],
      components: [
        component("major_road_access", 85, 0.3, { selected_access_point: "TPE via Pasir Ris Dr 3", peak_duration_minutes: 8 }, [{ selected: true, name: "Pasir Ris Drive 3", expressway: "TPE", peak_duration_minutes: 8 }]),
        component("route_connectivity", 89, 0.25, { distinct_expressways_reached: 3, independent_alternatives: 1, partially_independent_alternatives: 1 }),
        component("peak_access_penalty", 95, 0.25, { penalty_minutes: 0.2 }, [{ selected_access_point: "Pasir Ris Drive 3", peak_duration_minutes: 8, off_peak_duration_minutes: 8, penalty_minutes: 0.2 }]),
        component("parking_convenience", 85, 0.2, { primary_carpark: { address: "BLK 744A PASIR RIS STREET 71", walk_minutes: 1, carpark_type: "MULTI_STOREY", sheltered_status: "YES", parking_system_type: "ELECTRONIC PARKING", night_parking: "NO", availability: { available_lots: 73, total_lots: 265, updated_at: new Date().toISOString(), status: "LIVE" } }, reasonable_carparks_within_250m: 2, reasonable_carparks_within_500m: 5 }),
      ],
    };
    const provisionalDriving = {
      ...completeDriving,
      overall_score: 82.1,
      display_score: 82.1,
      unrounded_score: 82.1,
      is_complete: false,
      coverage_ratio: 0.7,
      excluded_components: ["parking_convenience"],
      components: completeDriving.components.filter((item) => item.name !== "parking_convenience"),
    };
    await page.route("**/api/v1/sessions/driving-ui", async (route) => {
      if (route.request().method() === "GET") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ session_id: "driving-ui", demo_mode: false, profile_saved: true, buyer_profile: { max_budget: 900000, main_transport_mode: "MAINLY_DRIVING", schools_matter: false, named_schools: [], named_school: null, priorities: ["DRIVING"] }, listings: [{ listing_id: "pasir", display_name: "745 Pasir Ris Street 71", address: "745 Pasir Ris Street 71", asking_price: 850000, floor_area_sqm: 103, flat_type: "4 ROOM" }, { listing_id: "yishun", display_name: "332 Yishun Ring Road", address: "332 Yishun Ring Road", asking_price: 780000, floor_area_sqm: 103, flat_type: "4 ROOM" }], listing_count: 2 }) });
      } else await route.continue();
    });
    await page.route("**/api/v1/sessions/driving-ui/comparison", async (route) => {
      await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ session_id: "driving-ui", listing_count: 2, can_compare: true, immediate_metrics: [{ listing_id: "pasir", metric_name: "asking_price", raw_value: 850000, unit: "SGD", status: "AVAILABLE", explanation: "" }, { listing_id: "yishun", metric_name: "asking_price", raw_value: 780000, unit: "SGD", status: "AVAILABLE", explanation: "" }], requirement_results: [], preference_scores: [], recommendation: null, fair_price_status: "NOT_STARTED", fair_price_by_listing: {}, transport_by_listing: {}, driving_by_listing: { pasir: completeDriving, yishun: provisionalDriving }, schools_by_listing: {}, observations: [], journey_results: [], enrichment_summary: [], demo_mode: false }) });
    });

    await page.goto("/session/driving-ui/comparison");
    await expect(page.getByRole("heading", { name: "Driving Connectivity" })).toBeVisible();
    await expect(page.getByText("745 Pasir Ris Street 71").first()).toBeVisible();
    await expect(page.getByText("332 Yishun Ring Road").first()).toBeVisible();
    await expect(page.locator('[aria-label="Driving connectivity: 90 out of 100, Complete"]')).toBeVisible();
    await expect(page.locator('[aria-label="Driving connectivity: 82 out of 100, Provisional"]')).toBeVisible();
    await expect(page.getByText("This score is provisional because one or more general driving components could not be assessed.")).toBeVisible();
    await expect(page.getByText(/Add a regular destination to compare journey times/).first()).toBeVisible();
    await expect(page.getByText("Driving breakdown").first()).toBeVisible();
    const drivingComparison = page.getByRole("heading", { name: "Compared with your shortlisted homes" }).last();
    await expect(drivingComparison).toBeVisible();
    await expect(page.getByText("Overall driving connectivity")).toBeVisible();
    await expect(page.getByText("Major-road access").last()).toBeVisible();
    const majorRoadButton = page.getByRole("button", { name: "Major-road access details" }).first();
    const parkingButton = page.getByRole("button", { name: "Parking convenience details" }).first();
    await expect(majorRoadButton).toHaveAttribute("aria-expanded", "false");
    await expect(parkingButton).toHaveAttribute("aria-expanded", "false");
    await expect(page.getByText("BLK 744A PASIR RIS STREET 71")).toHaveCount(0);
    await majorRoadButton.press("Enter");
    await expect(majorRoadButton).toHaveAttribute("aria-expanded", "true");
    await expect(majorRoadButton.locator("svg")).toHaveClass(/rotate-180/);
    await expect(page.getByText("What it means").first()).toBeVisible();
    await expect(page.getByText("Your result").first()).toBeVisible();
    await expect(page.getByText("Supporting details").first()).toBeVisible();
    await parkingButton.click();
    await expect(parkingButton).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByText("BLK 744A PASIR RIS STREET 71").first()).toBeVisible();
    await expect(page.getByText("Current availability snapshot").first()).toBeVisible();
    await expect(page.getByText("View technical assessment details")).toHaveCount(0);
    await expect(page.getByText("Estimate unavailable").first()).toBeVisible();
    await expect(page.getByText("90/100 · Excellent").first()).toBeVisible();
    await expect(page.getByText("82/100 · Good").first()).toBeVisible();
    await expect(page.getByText(/UNKNOWN|partial\)/i)).toHaveCount(0);
  });
});

test.describe("Mobile comparison", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("comparison visible on mobile", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: /start new comparison/i }).click();
    await page.getByRole("button", { name: /save profile/i }).click();

    for (const price of ["640000", "660000"]) {
      await page.getByLabel(/address/i).fill(`Blk ${price} Bishan St 12`);
      await page.getByLabel(/asking price/i).fill(price);
      await page.getByLabel(/floor area/i).fill("91");
      await page.getByRole("button", { name: /add listing/i }).click();
    }

    await expect(page.getByText(/Price and affordability/)).toBeVisible();
  });
});

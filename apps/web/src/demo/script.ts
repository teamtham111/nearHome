export type DemoAction = "click" | "type" | "copy" | "paste" | "wait" | "navigate";

export type DemoStep = {
  id: string;
  route: string;
  stage: number;
  action: DemoAction;
  target?: string;
  value?: string;
  caption: string;
  why: string;
  holdMs?: number;
  pauseAfter?: boolean;
};

export const DEMO_STEPS: DemoStep[] = [
  { id: "start", route: "/", stage: 1, action: "click", target: "start-comparison", caption: "Start your comparison", why: "NearHome begins with the factors that matter to your household.", pauseAfter: true },
  { id: "budget", route: "/session/demo-session", stage: 2, action: "type", target: "budget", value: "750000", caption: "Set your maximum budget", why: "This gives the comparison a clear affordability ceiling." },
  { id: "transport", route: "/session/demo-session", stage: 2, action: "click", target: "transport-both", caption: "Choose your main transport mode", why: "Both modes lets the results show public transport and driving connectivity." },
  { id: "priority-1", route: "/session/demo-session", stage: 2, action: "click", target: "priority-add", caption: "Add your first priority: affordability", why: "Pick the factor that matters most to your household. NearHome weighs your first priority most heavily." },
  { id: "priority-2", route: "/session/demo-session", stage: 2, action: "click", target: "priority-add", caption: "Add your second priority: space", why: "Order matters. Balance staying in budget with the room your household needs." },
  { id: "priority-3", route: "/session/demo-session", stage: 2, action: "click", target: "priority-add", caption: "Add your third priority: public transport", why: "You can rank up to three factors, from most to least important." },
  { id: "destination", route: "/session/demo-session", stage: 2, action: "click", target: "destination-add", caption: "Add a regular destination", why: "A usual destination lets you compare the journeys from each flat." },
  { id: "schools", route: "/session/demo-session", stage: 2, action: "click", target: "schools-toggle", caption: "Turn on schools", why: "Switch this on to see which schools are near each flat, and to add schools you care about." },
  { id: "school-search", route: "/session/demo-session", stage: 2, action: "type", target: "school-query", value: "Ang Mo Kio Primary School", caption: "Add a school you want your children to attend", why: "Search for a specific school and select it. At the end, NearHome shows how far each flat is from it, and whether it is within 2 km." },
  { id: "save-profile", route: "/session/demo-session", stage: 2, action: "click", target: "save-profile", caption: "Save your preferences", why: "Your budget, priorities, destination and schools are saved, then you can add the flats you are considering." },
  { id: "copy-flat-1", route: "/demo/listing/1", stage: 3, action: "copy", target: "mock-listing", caption: "Copy the first listing", why: "The mock listing demonstrates the copy step without connecting to a property site.", pauseAfter: true },
  { id: "paste-flat-1", route: "/session/demo-session", stage: 3, action: "paste", target: "listing-text", value: "1", caption: "Paste the listing into NearHome", why: "Smart Paste reads the price, floor area and address for you to review.", pauseAfter: true },
  { id: "review-flat-1", route: "/session/demo-session", stage: 3, action: "type", target: "flag-lease", value: "63", caption: "Check the flagged field", why: "Smart Paste could not find the remaining lease in this listing, so it flags the field instead of guessing. Type it in from the listing." },
  { id: "confirm-flat-1", route: "/session/demo-session", stage: 3, action: "click", target: "confirm-listing", caption: "Confirm flat one", why: "Once the details look right, confirm to add the flat to your shortlist.", pauseAfter: true },
  { id: "copy-flat-2", route: "/demo/listing/2", stage: 3, action: "copy", target: "mock-listing", caption: "Copy the second listing", why: "A second flat gives NearHome a shortlist to compare.", pauseAfter: true },
  { id: "paste-flat-2", route: "/session/demo-session", stage: 3, action: "paste", target: "listing-text", value: "2", caption: "Paste and confirm flat two", why: "This time every field was found, so the review is quick. Confirm to add it.", pauseAfter: true },
  { id: "run", route: "/session/demo-session", stage: 4, action: "click", target: "run-comparison", caption: "Run the comparison", why: "NearHome brings your preferences and both flats together." , pauseAfter: true},
  { id: "results-loading", route: "/session/demo-session/comparison", stage: 5, action: "wait", target: "enrichment-progress-bar", caption: "Preparing your comparison", why: "NearHome checks both flats. In this demo, the loading bar finishes quickly so you can explore the results.", holdMs: 1200 },
  { id: "results-overview", route: "/session/demo-session/comparison", stage: 5, action: "wait", target: "comparison-results", caption: "Your comparison is ready", why: "Compare the details from both listings and see the trade-offs side by side.", holdMs: 1600 },
  { id: "results-price", route: "/session/demo-session/comparison", stage: 5, action: "wait", target: "comparison-price", caption: "Compare price and affordability", why: "See asking price, budget headroom, floor area and lease side by side.", holdMs: 2000 },
  { id: "results-fair-price", route: "/session/demo-session/comparison", stage: 5, action: "wait", target: "comparison-fair-price", caption: "Compare prices with nearby sales", why: "See each asking price beside the average of five recent nearby HDB transactions.", holdMs: 1800 },
  { id: "results-fair-price-comparables", route: "/session/demo-session/comparison", stage: 5, action: "wait", target: "comparison-fair-price-comparables", caption: "See the five recent comparable sales", why: "These nearby HDB resale records show the recent prices behind the demo estimate; the asking price is shown against their average.", holdMs: 2400 },
  { id: "results-transport", route: "/session/demo-session/comparison", stage: 5, action: "wait", target: "comparison-transport", caption: "Compare public transport", why: "Review public transport connectivity and the available transport evidence.", holdMs: 2000 },
  { id: "results-driving", route: "/session/demo-session/comparison", stage: 5, action: "wait", target: "comparison-driving", caption: "Compare driving access", why: "See how convenient driving is from each home, separately from a specific commute.", holdMs: 2000 },
  { id: "results-schools", route: "/session/demo-session/comparison", stage: 5, action: "wait", target: "comparison-schools", caption: "Review nearby schools", why: "See nearby-school coverage for each flat, and whether the school you chose is within 2 km.", holdMs: 3500 },
];

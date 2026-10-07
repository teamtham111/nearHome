export type EvaluationGuide = {
  id: "transport" | "driving" | "fair-price" | "schools";
  icon: string;
  label: string;
  manualChecks: string[];
  title: string;
  description: string;
  metrics: string[][];
  takeaway: string;
  disclosures: Array<{ title: string; text?: string; items?: string[] }>;
};

export const evaluationGuides: EvaluationGuide[] = [
  {
    id: "transport",
    icon: "◎",
    label: "Public transport",
    manualChecks: [
      "Find nearby bus stops and stations. Check the walking routes.",
      "Look up the buses at each stop and trace where they go.",
      "Check MRT connections and alternative travel options.",
    ],
    title: "See what nearby transport unlocks.",
    description: "NearHome follows the network you can realistically enter, then shows the evidence and fixed rules behind the result.",
    metrics: [["First-mile entry", "Routed walk + scheduled wait"], ["Bus coverage", "Direct + same-stop transfers"], ["MRT reach", "Rail-network paths"], ["Fallback options", "Independent routes"]],
    takeaway: "Compare the transport network around a flat, not just the distance to one station.",
    disclosures: [
      { title: "What we measure", items: ["Lowest-friction access route — NearHome tries three ways to enter the network: walk to a usable bus corridor, walk directly to rail, or walk to a bus stop then take a feeder bus to rail. It keeps the route with the lowest calculated access cost; this may not be the geographically nearest station.", "Direct bus corridor — a usable bus service you can board from a qualifying nearby stop and stay on without changing buses. Services travelling substantially the same ordered downstream stops are grouped together, so several overlapping buses do not exaggerate coverage.", "One-transfer corridor — a genuinely new corridor unlocked by changing buses after boarding the first service. The transfer must happen at the exact same LTA stop code; NearHome does not assume you can walk between merely nearby stops.", "MRT reach — the number of physical MRT/LRT stations reached from the selected practical rail entry, grouped by whether the shortest rail path needs no transfer, one transfer, or more transfers.", "Route resilience — genuinely different fallbacks: bus plus rail access, a second practical station, a rail line not available at the primary station, or up to two separate direct bus corridors.", "Routed catchments — a bus stop needs a confirmed walk of 10 minutes or less to count for Access. For the stricter Bus Coverage component, it must also be within 400 routed metres."] },
      { title: "How it’s scored", items: ["Overall weighting — Access 30% · Bus coverage 25% · MRT reach 30% · Route resilience 15%.", "Access cost — walking minutes count ×1.25, scheduled waiting ×1.50, then NearHome adds in-vehicle time, a 2-minute station-entry allowance and a flat 6-minute penalty if the route changes buses before rail (at most one such change is allowed). Lower cost means a higher Access score.", "Access bands — 95 at ≤4 minutes; 88 at ≤7; 80 at ≤10; 68 at ≤15; 55 at ≤20; 38 at ≤30; and 20 above 30 minutes of generalised access cost.", "Bus coverage — 70% comes from direct corridors and 30% from new one-transfer corridors. Direct coverage reaches its highest band at 8 corridors; one-transfer coverage reaches its highest band at 12. A corridor only counts when its combined AM-peak scheduled maximum interval is 15 minutes or less.", "MRT reach — Dijkstra’s shortest paths run through the curated rail graph. NearHome separately counts stations reachable with no transfer, one transfer and two or more transfers within 30 structural minutes, plus additional stations reached in 31–45 structural minutes.", "Route resilience — no confirmed independent fallback scores 15. Otherwise the formula is min(100, 20 + 16 × independent fallback units), up to five units.", "The result comes from fixed rules and evidence — never an AI-generated rating."] },
      { title: "Data and limits", items: ["Walking and feeder routes are confirmed through the routing provider; straight-line distance only shortlists candidates and never decides the result on its own.", "Bus routes and scheduled frequencies come from LTA reference data. Scheduled waiting is an estimate based on the published frequency range, not a real-time arrival prediction.", "Rail reach uses structural graph minutes: bundled ride edges are 1.5–2.5 minutes and bundled interchange edges are 5–6 minutes. It is not a live train timetable.", "If route or reference data is missing, NearHome marks the affected component as unavailable. It does not convert missing evidence into zero or fabricate a replacement score."] },
    ],
  },
  {
    id: "driving",
    icon: "▱",
    label: "Driving",
    manualChecks: [
      "Locate the roads and expressway entrances near each flat.",
      "Compare likely routes to the places you visit regularly.",
      "Check how traffic and departure time may change each journey.",
    ],
    title: "Follow the roads beyond the block.",
    description: "Review road access and estimated journeys to your regular destinations, alongside the limits of each estimate.",
    metrics: [["Road access", "Major roads nearby"], ["Regular journeys", "Compared by flat"], ["Travel time", "Estimate, not a guarantee"]],
    takeaway: "A nearby expressway is only part of the picture; compare the full route to where you go.",
    disclosures: [
      { title: "What we measure", items: ["Major-road access — the confirmed driving time from the flat to a legal, pre-mapped entry point onto an SLA-designated major road. A road that merely looks close on the map does not count.", "Route flexibility — genuinely different practical ways to reach the main road network. Routes sharing 30% or less of their road names count as independent; routes sharing 31–70% count as partly independent. An alternative more than 15 minutes slower is not counted.", "Peak-hour reliability — the extra time for the same major-road entry at 8am compared with 10pm. This asks how much congestion changes access from home, not how long a particular work commute takes.", "Home parking convenience — the walked route to up to five official HDB carparks initially found within 500 metres. A carpark that takes more than 12 minutes to walk to is treated as impractical; a nearby HDB carpark does not guarantee a resident space.", "Personal journeys — destinations you add are shown separately. They help compare your own trips but do not change the neighbourhood driving score."] },
      { title: "How it’s scored", items: ["Overall weighting — Major-road access 30% · Route flexibility 25% · Peak-hour reliability 25% · Home parking 20%.", "Major-road access — a confirmed route of 4 minutes or less scores 95; ≤7 scores 85; ≤10 scores 72; ≤15 scores 58; ≤22 scores 42; longer routes score 25.", "Route flexibility — the score increases with up to three distinct major roads, up to four independent alternatives and up to four partly independent alternatives. It is capped at 95.", "Peak-hour reliability — 2 or fewer extra minutes scores 95; ≤5 scores 85; ≤10 scores 70; ≤15 scores 55; ≤25 scores 40; more than 25 extra minutes scores 25.", "A component can contribute only when the needed evidence exists. At least 60% of the total component weight must be available before driving can contribute to the overall priority fit."] },
      { title: "Data and limits", items: ["Routes come from the routing provider, using the flat’s mapped coordinates and precomputed major-road entry points. They are estimates, not a promise of a future journey time.", "Traffic-aware routes are checked at the defined 8am and 10pm comparison times. Live traffic can still change after a result is calculated.", "Carpark location data and parking availability are separate. Missing or stale availability is marked unavailable rather than shown as current.", "If routing, coordinates or reference data is unavailable, NearHome shows the affected component as unavailable instead of making up a score."] },
    ],
  },
  {
    id: "fair-price",
    icon: "⌁",
    label: "Fair price",
    manualChecks: [
      "Search recent resale transactions in the same town.",
      "Compare flat type, floor area, lease and transaction timing.",
      "Check how the asking price sits against relevant evidence.",
    ],
    title: "Put the asking price in context.",
    description: "NearHome measures a fair-price reference from historical resale patterns, with comparable transactions to show the local evidence.",
    metrics: [["Fair price", "Comparable transactions"], ["Confidence", "Strength of the evidence"]],
    takeaway: "An estimate helps you ask better questions; it is not an official HDB valuation.",
    disclosures: [
      { title: "What we measure", items: ["Estimated market value — a reference value based on historical HDB resale patterns and the confirmed listing details. It is not a recommended offer price.", "Asking-price difference — the listing’s asking price minus the estimated value, shown in dollars and as a percentage. A positive difference means the asking price is above the estimate.", "Comparable transactions — similar resale records that give local market context. They are selected for factors such as recency, area, remaining lease, location and, where available, storey.", "Confidence — how strong the comparable evidence is: its effective amount, similarity, recency, spread, missing optional fields and any relaxed filters. It is not a guarantee that the estimate is correct.", "Budget and affordability — considered elsewhere in your comparison. They do not change the fair-price estimate."] },
      { title: "How it’s calculated", items: ["Primary method — the app’s CatBoost valuation model estimates value from historical transactions and listing details. The visible comparable rows provide evidence and context; they are not a small average used on their own to produce the primary estimate.", "Fallback method — if the CatBoost model cannot produce a prediction, NearHome uses weighted comparable transactions instead. More relevant and more recent records carry more weight.", "Likely range — shown alongside the central estimate using the saved residual interval from the valuation model. It is a range of uncertainty, not a promised selling price.", "Price labels — within 2% is ‘close to estimated market value’; >2–5% is ‘slightly’ above/below; >5–10% is above/below; and >10% is significantly above/below. These are interpretation labels, not confidence levels.", "If neither the model nor defensible comparable evidence can produce an estimate, NearHome marks fair price unavailable rather than guessing."] },
      { title: "Data and limits", items: ["The model is trained on historical HDB resale transactions. It cannot account for future market changes, seller circumstances or details that are not in the available listing evidence.", "The exact remaining lease and floor area are required. If either is missing or invalid, no valuation is produced.", "A CatBoost estimate can still be shown when no defensible comparable rows are available, but NearHome warns that the local comparable evidence is unavailable and lowers confidence accordingly.", "This is decision support only — not an official HDB valuation, loan valuation or offer-price recommendation."] },
    ],
  },
  {
    id: "schools",
    icon: "◇",
    label: "Schools",
    manualChecks: [
      "Find nearby schools and the routes from each flat.",
      "Check the schools that matter to your household.",
      "Confirm current details with official school sources.",
    ],
    title: "Bring school locations into view.",
    description: "NearHome measures nearby-school proximity within 2 km of each flat, then lets you check a named school separately.",
    metrics: [["School proximity", "Within 2 km of the flat"]],
    takeaway: "Use proximity as context, then confirm requirements and admissions details with the school.",
    disclosures: [
      { title: "What we measure", items: ["Nearby schools — the official school locations within 2 km of the flat. NearHome shows the number within 1 km, the number within 2 km and the nearest school.", "Named schools — the particular schools you enter, matched only when the name resolves to one official school. NearHome recognises the supported aliases RI/RJC for Raffles Institution and HCI for Hwa Chong Institution.", "Distance — straight-line geographic distance from the flat’s coordinates to the school’s coordinates. It is not a walking route, travel-time estimate or a measure of school quality.", "Admissions — proximity is only location context. It does not predict admission, registration priority or eligibility."] },
      { title: "How it’s scored", items: ["Schools within 1 km — each of the first five schools counts for 15 points, for up to 75 points.", "Additional schools from more than 1 km to 2 km — each of the first five counts for 5 points, for up to 25 points.", "Total score — the school-proximity score is capped at 100. It reflects nearby-school counts only; named-school matches and admissions are not added as extra points.", "A named school is reported only when there is one unambiguous official match. NearHome does not guess between several similarly named schools."] },
      { title: "Data and limits", items: ["School names, levels and coordinates come from the MOE reference snapshot. Duplicate entries with the same name and location are removed before counting.", "NearHome needs mapped coordinates for the flat. If they are unavailable, the school result and score are unavailable rather than treated as zero.", "A named school that is not found in the reference snapshot is explicitly flagged. Its distance is not invented.", "School locations and simple distance bands should be checked against the latest official admissions and school information before making a decision."] },
    ],
  },
] as const;

export type Band = { label: string; value: number; text?: string };
export type BandGroup = { title: string; note?: string; barMax: number; unit: string; bands: Band[] };
export type Term = { term: string; meaning: string };
export type DiagramId =
  | "access-routes"
  | "bus-overlap"
  | "rail-map"
  | "major-road-entry"
  | "independence"
  | "peak-penalty"
  | "school-rings"
  | "price-range";

export type ScoreComponent = {
  id: string;
  label: string;
  // Share of the category score. Omitted where the category is not a weighted sum.
  pct?: number;
  // One short line, shown for every component above the explorer so the page reads without clicking.
  oneLine: string;
  measures: string;
  // One sentence on why the rule exists. Reasons describe what the code does, not research findings.
  why: string;
  groups: BandGroup[];
  facts?: string[];
  terms: Term[];
  diagrams?: DiagramId[];
  // Optional headings over the diagrams and over the score-band card.
  diagramTitle?: string;
  groupsTitle?: string;
  example?: { title: string; steps: string[]; result: string };
};

export type CategoryTotal =
  | { kind: "weighted"; scores: Record<string, number>; unit?: undefined }
  | { kind: "sum"; scores: Record<string, number> }
  | { kind: "note" };

export type Methodology = {
  id: EvaluationGuide["id"];
  label: string;
  question: string;
  selectorTitle: string;
  components: ScoreComponent[];
  limits: Array<{ ok: boolean; text: string }>;
  // What happens inside the category when a part has no evidence.
  missing: string[];
  total: CategoryTotal;
};

// Plain-language summaries shown on the methodology page. Every figure comes from the scoring code in
// apps/api/app/engines (and the full rules above), so update both together when a rule changes.
export const methodology: Methodology[] = [
  {
    id: "transport",
    label: "Public transport",
    question: "How practical is it to get around without a car?",
    selectorTitle: "How the score is weighted",
    components: [
      {
        id: "access",
        label: "Access",
        pct: 30,
        measures: "How easily you can step onto the public transport network from the flat. NearHome tries three ways in and keeps the one that takes the least effort, which is not always the nearest station.",
        oneLine: "Effort to get from your door onto the network",
        why: "Walking counts ×1.25 and waiting ×1.5 so that time spent outside the vehicle weighs more than time spent riding.",
        groups: [{
          title: "Access score bands",
          note: "Lower access cost means a higher score.",
          barMax: 100,
          unit: "",
          bands: [{ label: "≤ 4 min", value: 95 }, { label: "≤ 7", value: 88 }, { label: "≤ 10", value: 80 }, { label: "≤ 15", value: 68 }, { label: "≤ 20", value: 55 }, { label: "≤ 30", value: 38 }, { label: "> 30", value: 20 }],
        }],
        terms: [
          { term: "Access route", meaning: "One of three ways to enter the network: walk to a bus corridor, walk straight to an MRT or LRT station, or walk to a bus stop and ride a feeder bus to a station." },
          { term: "Access cost", meaning: "One effort figure, in minutes. Walking counts ×1.25 and scheduled waiting ×1.5. Then we add the ride time and 2 minutes to enter a station. A bus change before rail adds a flat 6 minutes, and only the bus-then-rail route can have one." },
          { term: "Scheduled waiting", meaning: "Half the average gap between buses on the corridor in the morning peak, from the published frequencies, and never more than 10 minutes. If several services share the corridor, their frequencies are combined. It is not a live arrival time." },
          { term: "Usable for access", meaning: "A corridor is ignored if its buses can be more than 15 minutes apart in the morning peak." },
        ],
        diagrams: ["access-routes"],
        example: {
          title: "Sample flat: a 5-minute walk to the MRT station",
          steps: ["Routed walk to the station: 5 minutes × 1.25 = 6.25", "Add the 2-minute station-entry allowance = 8.25", "8.25 falls in the “≤ 10” band"],
          result: "Access scores 80 out of 100",
        },
      },
      {
        id: "bus-coverage",
        label: "Bus coverage",
        pct: 25,
        measures: "How many genuinely different bus corridors you can reach from bus stops within a 400 metre routed walk.",
        oneLine: "How many different bus corridors you can use",
        why: "Corridors are counted instead of bus numbers so overlapping services on one road do not inflate coverage.",
        groups: [
          {
            title: "Direct corridors (70% of bus coverage)",
            note: "Corridors you can board and ride without changing buses.",
            barMax: 100,
            unit: "",
            bands: [{ label: "8 or more", value: 95 }, { label: "5 to 7", value: 85 }, { label: "3 to 4", value: 72 }, { label: "2", value: 60 }, { label: "1", value: 45 }, { label: "None", value: 0 }],
          },
          {
            title: "One-transfer corridors (30% of bus coverage)",
            note: "New corridors unlocked by changing buses once.",
            barMax: 100,
            unit: "",
            bands: [{ label: "12 or more", value: 90 }, { label: "6 to 11", value: 78 }, { label: "3 to 5", value: 62 }, { label: "1 to 2", value: 45 }, { label: "None", value: 30 }],
          },
        ],
        facts: ["A corridor only counts if its buses come at least every 15 minutes in the morning peak.", "A stop only counts if the actual walking route to it is 400 metres or less, not the straight-line distance."],
        terms: [
          { term: "Corridor", meaning: "A group of bus services that run along substantially the same stops, in the same order. Services that overlap by 70% or more of the shorter route count once." },
          { term: "Direct corridor", meaning: "A corridor you can board from a nearby stop and stay on, with no bus change." },
          { term: "One-transfer corridor", meaning: "A new corridor reached by changing at the exact same LTA stop code. We never assume you can walk between nearby stops." },
        ],
        diagrams: ["bus-overlap"],
        diagramTitle: "How do we define corridors?",
        groupsTitle: "How corridors become a score",
        example: {
          title: "Sample flat: 5 direct and 6 one-transfer corridors",
          steps: ["5 direct corridors falls in the “5 to 7” band = 85", "6 one-transfer corridors falls in the “6 to 11” band = 78", "85 × 0.70 + 78 × 0.30 = 59.5 + 23.4"],
          result: "Bus coverage scores 82.9 out of 100",
        },
      },
      {
        id: "mrt-reach",
        label: "MRT reach",
        pct: 30,
        measures: "How many MRT and LRT stations you can reach from the station you would use, grouped by how many train changes each one needs. Fewer changes score higher.",
        oneLine: "How many stations you can reach, and with how many changes",
        why: "Stations are split by transfers so one that needs no change counts for more than one that needs two.",
        groups: [
          { title: "No-transfer stations within 30 min (35% of MRT reach)", note: "Reached within 30 structural minutes with no change of line. Full marks at 20 stations.", barMax: 100, unit: "", bands: [{ label: "5 stations", value: 25 }, { label: "10", value: 50 }, { label: "15", value: 75 }, { label: "20 or more", value: 100 }] },
          { title: "One-transfer stations within 30 min (35%)", note: "Reached within 30 structural minutes with one change of line. Full marks at 45 stations.", barMax: 100, unit: "", bands: [{ label: "10 stations", value: 22 }, { label: "20", value: 44 }, { label: "30", value: 67 }, { label: "45 or more", value: 100 }] },
          { title: "Two-or-more-transfer stations within 30 min (10%)", note: "Reached within 30 structural minutes with two or more changes of line. Full marks at 20 stations.", barMax: 100, unit: "", bands: [{ label: "5 stations", value: 25 }, { label: "10", value: 50 }, { label: "20 or more", value: 100 }] },
          { title: "Extra stations, 31 to 45 min (20%)", note: "Reached in 31 to 45 structural minutes, with any number of changes. Full marks at 60 stations.", barMax: 100, unit: "", bands: [{ label: "15 stations", value: 25 }, { label: "30", value: 50 }, { label: "45", value: 75 }, { label: "60 or more", value: 100 }] },
        ],
        terms: [
          { term: "Structural minutes", meaning: "Travel time measured on NearHome’s rail map. Each ride between stations adds 1.5 to 2.5 minutes and each interchange 5 to 6 minutes. It is not a live timetable." },
          { term: "Transfer", meaning: "A change from one rail line to another. Stations are grouped by the fewest transfers needed to reach them." },
          { term: "Practical rail entry", meaning: "The station you would realistically start from, chosen by Access, not just the closest one." },
        ],
        diagrams: ["rail-map"],
        example: {
          title: "Sample flat: stations reachable by number of transfers",
          steps: ["20 no-transfer stations = 100 × 0.35 = 35.0", "30 one-transfer stations = 66.7 × 0.35 = 23.3", "10 two-or-more-transfer stations = 50 × 0.10 = 5.0", "30 extra stations = 50 × 0.20 = 10.0"],
          result: "MRT reach scores 73.3 out of 100",
        },
      },
      {
        id: "fallbacks",
        label: "Fallbacks",
        pct: 15,
        measures: "Whether you would still have a different way to travel if your usual route were disrupted. Each genuinely different option adds to the score.",
        oneLine: "Whether you have a different way to travel",
        why: "A flat with a second route is less exposed to a single disruption than one that depends on one line or bus.",
        groups: [{
          title: "Score by number of fallback units",
          note: "20 + 16 points per unit, up to five units. With none, the score is 15.",
          barMax: 100,
          unit: "",
          bands: [{ label: "None", value: 15 }, { label: "1 unit", value: 36 }, { label: "2", value: 52 }, { label: "3", value: 68 }, { label: "4", value: 84 }, { label: "5", value: 100 }],
        }],
        facts: [
          "Different access mode, 1 unit: you can start by bus or by train, so if one is disrupted the other still gets you onto the network.",
          "A second station, 1 unit: at least one other practical MRT station besides your main one. Several extra stations still count once.",
          "A different rail line, 1 unit: another station gives you a line your main station does not have. Several new lines still count once.",
          "Separate bus corridors, up to 2 units: each distinct direct bus corridor adds one unit, and no more than two count.",
          "The most a flat can earn is 5 units.",
        ],
        terms: [
          { term: "Fallback unit", meaning: "One genuinely different way to keep travelling if your usual route stops. Units are counted by what is different (mode, station, line or corridor), not by how many routes exist." },
          { term: "Practical entry", meaning: "A bus corridor or station you can reach on a confirmed walking route. For a station, that means a walk of 20 minutes or less." },
        ],
        example: {
          title: "Sample flat: near Bishan MRT, with Braddell MRT as the second station (illustrative)",
          steps: [
            "Bus access and rail access are both practical = 1 unit",
            "A second practical station, Braddell MRT = 1 unit",
            "Braddell is also on the North-South line, which Bishan already has, so there is no new rail line = 0 units",
            "5 direct bus corridors are available, but only 2 can count = 2 units",
            "Total 4 units, so 20 + 4 × 16 = 84",
          ],
          result: "Fallbacks score 84 out of 100",
        },
      },
    ],
    limits: [
      { ok: true, text: "Bus routes and frequencies come from LTA reference data" },
      { ok: true, text: "Walking routes are confirmed by the routing provider" },
      { ok: false, text: "Waiting time is scheduled, not live arrivals" },
      { ok: false, text: "Rail time is structural, not a train timetable" },
    ],
    missing: [
      "A part without evidence is shown as unavailable. It is never counted as zero.",
      "The category score averages the parts that were assessed, with their weights rescaled to add up to 100%.",
      "That score counts toward your ranking only when the assessed parts make up at least 60% of the total weight. Otherwise it is shown for information and marked as partial.",
    ],
    total: { kind: "weighted", scores: { access: 80, "bus-coverage": 82.9, "mrt-reach": 73.3, fallbacks: 84 } },
  },
  {
    id: "driving",
    label: "Driving",
    question: "How practical is it to get around by car?",
    selectorTitle: "How the score is weighted",
    components: [
      {
        id: "major-road",
        label: "Major-road access",
        pct: 30,
        measures: "How quickly you can get from the flat onto a major road. It uses a confirmed driving route to a legal entry point, not a road that merely looks close on the map.",
        oneLine: "Drive time to a legal entry onto a major road",
        why: "Only a confirmed route to a real entry point counts, so a road that is close on the map but cannot be joined does not raise the score.",
        groups: [{
          title: "Major-road access bands",
          barMax: 100,
          unit: "",
          bands: [{ label: "≤ 4 min", value: 95 }, { label: "≤ 7", value: 85 }, { label: "≤ 10", value: 72 }, { label: "≤ 15", value: 58 }, { label: "≤ 22", value: 42 }, { label: "> 22", value: 25 }],
        }],
        terms: [
          { term: "Major road", meaning: "A road designated as a major road by the Singapore Land Authority, such as an expressway." },
          { term: "Entry point", meaning: "A pre-mapped junction or slip road where a car can legally join the major road. A road you can only cross or pass beneath does not count." },
        ],
        diagrams: ["major-road-entry"],
        example: {
          title: "Sample flat: a 6-minute drive to the PIE entry",
          steps: ["Confirmed drive to the PIE entry point: 6 minutes", "6 minutes falls in the “≤ 7” band"],
          result: "Major-road access scores 85 out of 100",
        },
      },
      {
        id: "route-flexibility",
        label: "Route flexibility",
        pct: 25,
        measures: "How many genuinely different ways you have to reach the main road network, so one closed road does not cut you off.",
        oneLine: "How many different ways to reach the main roads",
        why: "A flat with several independent routes is less affected when one road is slow or closed.",
        groups: [
          { title: "Distinct major roads reached", note: "12 points each, up to 3 roads.", barMax: 36, unit: " pts", bands: [{ label: "1 road", value: 12 }, { label: "2", value: 24 }, { label: "3 or more", value: 36 }] },
          { title: "Independent alternatives", note: "6 points each, up to 4.", barMax: 36, unit: " pts", bands: [{ label: "1", value: 6 }, { label: "2", value: 12 }, { label: "3", value: 18 }, { label: "4 or more", value: 24 }] },
          { title: "Partly independent alternatives", note: "3 points each, up to 4.", barMax: 36, unit: " pts", bands: [{ label: "1", value: 3 }, { label: "2", value: 6 }, { label: "3", value: 9 }, { label: "4 or more", value: 12 }] },
        ],
        facts: ["The score starts at 50 and adds the points above, capped at 95.", "An alternative that is more than 15 minutes slower is not counted."],
        terms: [
          { term: "Independent alternative", meaning: "A route that shares 30% or less of its road names with the main route." },
          { term: "Partly independent alternative", meaning: "A route that shares 31% to 70% of its road names with the main route." },
        ],
        diagrams: ["independence"],
        example: {
          title: "Sample flat: two major roads, two independent and one partly independent route",
          steps: ["Base score: 50", "2 major roads × 12 = 24", "2 independent alternatives × 6 = 12", "1 partly independent alternative × 3 = 3"],
          result: "Route flexibility scores 89 out of 100",
        },
      },
      {
        id: "peak-reliability",
        label: "Peak reliability",
        pct: 25,
        measures: "How much congestion changes your access to the major road. It compares the same trip at 8am and at 10pm. It is about leaving home, not about a particular commute.",
        oneLine: "Extra drive time at 8am compared with 10pm",
        why: "Comparing the same trip at two times isolates congestion from distance, so a flat is not marked down just for being far.",
        groups: [{
          title: "Peak penalty bands",
          barMax: 100,
          unit: "",
          bands: [{ label: "≤ 2 min extra", value: 95 }, { label: "≤ 5", value: 85 }, { label: "≤ 10", value: 70 }, { label: "≤ 15", value: 55 }, { label: "≤ 25", value: 40 }, { label: "> 25", value: 25 }],
        }],
        terms: [
          { term: "Peak penalty", meaning: "The extra minutes the same route takes at 8am compared with 10pm." },
        ],
        diagrams: ["peak-penalty"],
        example: {
          title: "Sample flat: 5 minutes at night, 8 at peak",
          steps: ["8 − 5 = 3 extra minutes", "3 minutes falls in the “≤ 5” band"],
          result: "Peak reliability scores 85 out of 100",
        },
      },
      {
        id: "parking",
        label: "Home parking",
        pct: 20,
        measures: "How convenient it is to park near home, based on official HDB carparks within 500 metres of the flat.",
        oneLine: "How easy it is to park near home",
        why: "Several factors are scored because a close carpark can still be small, restricted or usually full.",
        groups: [
          { title: "Walk to the nearest carpark (35% of parking)", note: "Up to 10 bonus points: 2 for each further reasonable carpark within 500 m. Carparks over a 12 minute walk are impractical.", barMax: 100, unit: "", bands: [{ label: "≤ 2 min", value: 100 }, { label: "≤ 4", value: 85 }, { label: "≤ 6", value: 70 }, { label: "≤ 8", value: 50 }, { label: "> 8", value: 25 }] },
          { title: "Carpark type (20%)", barMax: 100, unit: "", bands: [{ label: "Basement", value: 100 }, { label: "Multi-storey", value: 82 }, { label: "Surface + multi-storey", value: 78 }, { label: "Other", value: 55 }, { label: "Surface", value: 48 }] },
          { title: "Restrictions (10%)", note: "Scored only when the carpark has any restriction data. Capped at 100.", barMax: 100, unit: " pts", bands: [{ label: "Starting score", value: 50 }, { label: "Short-term parking allowed", value: 25 }, { label: "Night parking allowed", value: 15 }, { label: "Electronic parking system", value: 10 }] },
          { title: "Capacity (15%)", note: "40 points plus up to 60 more, rising with the number of car lots up to 500.", barMax: 100, unit: "", bands: [{ label: "No lots recorded", value: 40 }, { label: "100 lots", value: 52 }, { label: "250", value: 70 }, { label: "500 or more", value: 100 }] },
          { title: "Typical availability (20%)", note: "The carpark’s median weekday availability from past readings, used as it is. Needs at least 5 readings.", barMax: 100, unit: "", bands: [{ label: "25% usually free", value: 25 }, { label: "50%", value: 50 }, { label: "75%", value: 75 }, { label: "100%", value: 100 }] },
        ],
        facts: ["Any of these five parts without data is left out and the others are rescaled to add up to 100%.", "A single live availability reading is shown but is not scored."],
        terms: [
          { term: "Reasonable carpark", meaning: "An official HDB carpark you can actually walk to from the flat, confirmed by a walking route." },
        ],
        example: {
          title: "Sample flat: nearest carpark 5 minutes away, one more in range",
          steps: [
            "Walking: 5 minutes is the “≤ 6” band = 70, plus 2 for one more carpark = 72, × 0.35 = 25.2",
            "Type: multi-storey = 82 × 0.20 = 16.4",
            "Restrictions: 50 + 25 short-term + 10 electronic = 85 × 0.10 = 8.5",
            "Capacity: 250 lots = 40 + 250 ÷ 500 × 60 = 70 × 0.15 = 10.5",
            "Typical availability: 60% = 60 × 0.20 = 12.0",
          ],
          result: "Home parking scores 72.6 out of 100",
        },
      },
    ],
    limits: [
      { ok: true, text: "Routes use the flat’s mapped coordinates and pre-mapped road entry points" },
      { ok: true, text: "Peak delay compares the same trip at 8am and 10pm" },
      { ok: false, text: "Live traffic can still change after a result is calculated" },
      { ok: false, text: "A nearby HDB carpark does not guarantee a resident space" },
    ],
    missing: [
      "A part without evidence is shown as unavailable. It is never counted as zero.",
      "The category score averages the parts that were assessed, with their weights rescaled to add up to 100%.",
      "That score counts toward your ranking only when the assessed parts make up at least 60% of the total weight. Otherwise it is shown for information and marked as partial.",
      "Inside home parking, any of the five sub-scores without data is left out and the rest are rescaled.",
    ],
    total: { kind: "weighted", scores: { "major-road": 85, "route-flexibility": 89, "peak-reliability": 85, parking: 72.6 } },
  },
  {
    id: "fair-price",
    label: "Fair price",
    question: "Is the asking price in line with recent sales?",
    selectorTitle: "How the price check works",
    components: [
      {
        id: "estimate",
        label: "Estimated value",
        measures: "A reference value for the flat, based on historical HDB resale patterns and the confirmed listing details. It is not a recommended offer price.",
        oneLine: "A reference value for the flat",
        why: "A model trained on past resale transactions can weigh the flat’s details together, which a plain average of nearby sales cannot.",
        groups: [],
        facts: ["Primary method: a valuation model trained on historical HDB resale transactions.", "Fallback: if the model cannot produce a prediction, weighted comparable transactions are used. More relevant and more recent sales count more.", "The exact remaining lease and floor area are required. Without them, no estimate is produced.", "Fair price is an estimate, a likely range and a label. It becomes a 0 to 100 score only if you rank it as one of your priorities."],
        diagrams: ["price-range"],
        terms: [
          { term: "Valuation model", meaning: "A statistical model that learns price patterns from past resale transactions and applies them to this flat’s details." },
        ],
      },
      {
        id: "comparables",
        label: "Comparable sales",
        measures: "Recent, similar resale records that give local market context beside the estimate.",
        oneLine: "Recent similar sales nearby",
        why: "Showing the sales behind the estimate lets you check the evidence yourself instead of trusting one number.",
        groups: [],
        facts: ["Sales are chosen for recency, area, remaining lease, location and, where available, storey.", "If few close matches exist, the filters are relaxed and confidence is lowered."],
        diagrams: ["price-range"],
        terms: [
          { term: "Comparable sale", meaning: "A recent resale of a similar flat nearby. The visible rows show the evidence around the estimate. They are not a small average that produces the estimate by themselves." },
          { term: "Relaxed filter", meaning: "A search condition that was loosened to find enough sales. It lowers confidence and is always disclosed." },
        ],
      },
      {
        id: "range",
        label: "Range and confidence",
        measures: "A likely range around the estimate, and how strong the evidence behind it is.",
        oneLine: "How uncertain the estimate is",
        why: "An estimate is never exact, so a range and a confidence level are shown beside it.",
        groups: [],
        facts: ["The likely range comes from the valuation model’s saved residual interval. It is a range of uncertainty, not a promised selling price.", "Confidence reflects how many comparable sales there are, how similar and recent they are, how spread out their prices are, and how many optional fields are missing."],
        diagrams: ["price-range"],
        terms: [
          { term: "Confidence", meaning: "How strong the comparable evidence is. It is not a guarantee that the estimate is correct." },
        ],
      },
      {
        id: "label",
        label: "Price label",
        measures: "A plain-language label comparing the asking price with the estimate. These are interpretation labels, not confidence levels.",
        oneLine: "How the asking price compares",
        why: "Words such as “slightly above” are used instead of “overpriced” because a difference alone does not prove a price is wrong.",
        groups: [{
          title: "How the asking price is labelled",
          barMax: 100,
          unit: "",
          bands: [{ label: "Within 2%", value: 100, text: "Close to estimated market value" }, { label: "2% to 5%", value: 75, text: "Slightly above or below estimate" }, { label: "5% to 10%", value: 50, text: "Above or below estimate" }, { label: "Over 10%", value: 25, text: "Significantly above or below estimate" }],
        }],
        terms: [
          { term: "Asking-price difference", meaning: "The asking price minus the estimate, in dollars and as a percentage. A positive difference means the asking price is above the estimate." },
        ],
        diagrams: ["price-range"],
        example: {
          title: "Sample flat: the asking price against the estimate",
          steps: ["Asking price: $838,000", "Estimated value: $832,600", "Difference: +$5,400, which is +0.6%"],
          result: "Within 2%, so it is “close to estimated market value”",
        },
      },
    ],
    limits: [
      { ok: true, text: "Trained on historical HDB resale transactions" },
      { ok: true, text: "Comparable sales are shown as local evidence" },
      { ok: false, text: "Not an official HDB valuation or an offer-price recommendation" },
      { ok: false, text: "Cannot see future market changes or seller circumstances" },
    ],
    missing: [
      "If neither the valuation model nor the comparable sales can produce an estimate, fair price is unavailable and has no effect on your ranking.",
      "If the exact remaining lease or floor area is missing, no estimate is produced.",
      "Missing optional listing fields do not block the estimate, but they lower its confidence.",
    ],
    total: { kind: "note" },
  },
  {
    id: "schools",
    label: "Schools",
    question: "How close are the schools that matter to you?",
    selectorTitle: "How the score is built",
    components: [
      {
        id: "within-1km",
        label: "Within 1 km",
        pct: 75,
        measures: "Official schools within 1 km of the flat. Each of the first five counts for 15 points, for up to 75.",
        oneLine: "Schools inside 1 km",
        why: "Each school within 1 km earns 15 points, three times the 5 points for one between 1 and 2 km, so nearer schools count for more.",
        groups: [{
          title: "Points for schools within 1 km",
          barMax: 75,
          unit: " pts",
          bands: [{ label: "1 school", value: 15 }, { label: "2", value: 30 }, { label: "3", value: 45 }, { label: "4", value: 60 }, { label: "5 or more", value: 75 }],
        }],
        terms: [
          { term: "Distance", meaning: "Straight-line distance from the flat’s coordinates to the school’s. It is not a walking route and not a measure of school quality." },
          { term: "Named school", meaning: "A school you add yourself. It is matched only when the name resolves to one official school. It is shown separately and adds no extra points." },
        ],
        diagrams: ["school-rings"],
        example: {
          title: "Sample flat: one school within 1 km",
          steps: ["1 school × 15 points"],
          result: "The inner ring adds 15 of the possible 75 points",
        },
      },
      {
        id: "one-to-two-km",
        label: "1 to 2 km",
        pct: 25,
        measures: "Additional schools more than 1 km but within 2 km. Each of the first five counts for 5 points, for up to 25.",
        oneLine: "Extra schools between 1 and 2 km",
        why: "Schools a little further away still count, but for less, and only the first five count so the total stays capped.",
        groups: [{
          title: "Points for schools between 1 and 2 km",
          barMax: 25,
          unit: " pts",
          bands: [{ label: "1 school", value: 5 }, { label: "2", value: 10 }, { label: "3", value: 15 }, { label: "4", value: 20 }, { label: "5 or more", value: 25 }],
        }],
        facts: ["The total is capped at 100. Proximity is context only and does not predict admission, registration priority or eligibility."],
        terms: [
          { term: "Within 2 km", meaning: "The distance we use to report whether a school you named is nearby." },
        ],
        diagrams: ["school-rings"],
        example: {
          title: "Sample flat: four more schools between 1 and 2 km",
          steps: ["1 school within 1 km = 15 points", "4 schools between 1 and 2 km × 5 = 20 points"],
          result: "School proximity scores 35 out of 100",
        },
      },
    ],
    limits: [
      { ok: true, text: "School names and locations come from the MOE reference snapshot" },
      { ok: true, text: "A school you name is matched only when there is one clear result" },
      { ok: false, text: "Distance is straight-line, not a walking route" },
      { ok: false, text: "Proximity says nothing about admission or eligibility" },
    ],
    missing: [
      "NearHome needs mapped coordinates for the flat. Without them the school result and score are unavailable.",
      "A school you name that is not in the reference list is flagged. Its distance is never invented.",
    ],
    total: { kind: "sum", scores: { "within-1km": 15, "one-to-two-km": 20 } },
  },
];

// ---------------------------------------------------------------------------------------------
// From evidence to your ranking
// ---------------------------------------------------------------------------------------------

// One sample flat is used for every worked example on the page.
export const sampleFlat = {
  name: "Sample flat",
  address: "Blk 214 Bishan Street 23, 4-room",
  askingPrice: 838000,
  estimate: 832600,
  floorAreaSqm: 112,
  remainingLeaseYears: 63,
  buyerBudget: 900000,
  // Public transport total is computed from the components above (see page.tsx).
};

export const pipelineSteps: Array<{ title: string; detail: string }> = [
  { title: "Evidence", detail: "Your listing, plus LTA, HDB and MOE data" },
  { title: "Component scores", detail: "Each part scored from 0 to 100" },
  { title: "Category score", detail: "The parts added up with fixed weights" },
  { title: "Your priorities", detail: "Your ranking sets 45, 35 and 20%" },
  { title: "Overall fit", detail: "A weighted average, 0 to 100" },
  { title: "Tie check", detail: "Within 3 points is a practical tie" },
];

export type RuleRow = { when: string; score: string; tone: "good" | "bad" | "neutral" };

export type PriorityScoreInfo = {
  id: "affordability" | "space" | "lease" | "journey" | "fair-price" | "category";
  label: string;
  input: string;
  // Column heading for the left side of the rule rows.
  column: string;
  rows: RuleRow[];
  detail?: string;
};

// Source: `_absolute_score` and `_collect_raw_values` in apps/api/app/engines/preference_scoring.py.
// Every result is clamped to stay between 0 and 100.
export const priorityScoreInfo: PriorityScoreInfo[] = [
  {
    id: "affordability", label: "Affordability", input: "Asking price against your maximum budget", column: "Asking price",
    rows: [
      { when: "75% of your budget or less", score: "100", tone: "good" },
      { when: "Each extra 1% of your budget above that", score: "−2 points", tone: "bad" },
      { when: "125% of your budget or more", score: "0", tone: "bad" },
    ],
  },
  {
    id: "space", label: "Space", input: "Floor area in square metres", column: "Floor area",
    rows: [
      { when: "60 sqm or less", score: "0", tone: "bad" },
      { when: "Each extra 1 sqm between 60 and 120", score: "+1.7 points", tone: "good" },
      { when: "120 sqm or more", score: "100", tone: "good" },
    ],
  },
  {
    id: "lease", label: "Lease", input: "Remaining lease in years", column: "Remaining lease",
    rows: [
      { when: "40 years or less", score: "0", tone: "bad" },
      { when: "Each extra year between 40 and 80", score: "+2.5 points", tone: "good" },
      { when: "80 years or more", score: "100", tone: "good" },
    ],
  },
  {
    id: "journey", label: "Journey to an important place", input: "Shortest available journey time, in minutes", column: "Journey time",
    rows: [
      { when: "15 minutes or less", score: "100", tone: "good" },
      { when: "Each extra minute between 15 and 75", score: "−1.7 points", tone: "bad" },
      { when: "75 minutes or more", score: "0", tone: "bad" },
    ],
  },
  {
    id: "fair-price", label: "Fair price", input: "How far the asking price is from the estimate", column: "Asking price vs estimate",
    rows: [
      { when: "Equal to the estimate", score: "50", tone: "neutral" },
      { when: "Each 1% below the estimate", score: "+5 points", tone: "good" },
      { when: "Each 1% above the estimate", score: "−5 points", tone: "bad" },
    ],
    detail: "The points above are for high confidence. The estimate’s confidence scales them: medium ×0.8 (4 points), low ×0.5 (2.5 points), anything else ×0.25.",
  },
  {
    id: "category", label: "Public transport, driving, schools", input: "The category score from this page", column: "Priority",
    rows: [
      { when: "Public transport", score: "Its category score", tone: "neutral" },
      { when: "Driving", score: "Its category score", tone: "neutral" },
      { when: "Schools", score: "Its school score", tone: "neutral" },
    ],
    detail: "Public transport and driving count only when at least 60% of their weight was assessed.",
  },
];

// Rating words follow ratingForScore() in lib/public-transport.ts, which the comparison page already uses.
export const scoreKeyBands: Array<{ min: number; max: number; hint: string }> = [
  { min: 85, max: 100, hint: "A clear strength" },
  { min: 70, max: 84, hint: "Works well for most buyers" },
  { min: 55, max: 69, hint: "Adequate, with some trade-offs" },
  { min: 40, max: 54, hint: "A weakness to weigh up" },
  { min: 0, max: 39, hint: "A real drawback" },
];

// Only things that no score reads. Checked against apps/api/app/engines.
export const notAssessed: string[] = [
  "Noise, for example from roads, rail or neighbours",
  "The condition of the flat, and any renovation needed or its cost",
  "Shops, hawker centres, parks and healthcare nearby",
  "Which way the block faces and how much sun the flat gets",
  "Future developments around the flat",
  "School quality, ballots or whether a child would be admitted",
];

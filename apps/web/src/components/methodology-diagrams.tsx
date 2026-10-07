import type { DiagramId } from "@/lib/methodology";

const ink = "#203b33";
const muted = "#58665d";
const pine = "#173d36";
const teal = "#3f7a6c";
const soft = "#cfe3dc";
const paper = "#fffdf7";
const rust = "#a54e39";
const amber = "#e2a74e";

type Props = { title: string; desc: string; height: number; width?: number; children: React.ReactNode };

function Frame({ title, desc, height, width = 640, children }: Props) {
  return (
    <svg className={`nh-method-svg${width < 640 ? " nh-method-svg-narrow" : ""}`} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={`${title}. ${desc}`} xmlns="http://www.w3.org/2000/svg">
      <title>{title}</title>
      <desc>{desc}</desc>
      <rect x="0.5" y="0.5" width={width - 1} height={height - 1} rx="10" fill={paper} stroke="#d3d8cd" />
      <g fontFamily="inherit" fontSize="13" fill={ink}>{children}</g>
    </svg>
  );
}

function AccessRoutes() {
  const rows = [
    { y: 84, steps: ["Walk", "Bus stop", "Bus corridor"], color: pine, label: "1  Walk to a bus corridor" },
    { y: 140, steps: ["Walk", "MRT / LRT station"], color: teal, label: "2  Walk straight to rail" },
    { y: 196, steps: ["Walk", "Bus stop", "Feeder bus", "Station"], color: amber, label: "3  Walk, feeder bus, then rail" },
  ];
  return (
    <Frame title="Three ways to enter the network" desc="NearHome tries walking to a bus corridor, walking straight to a station, or walking to a bus stop then taking a feeder bus to a station, and keeps the one with the lowest access cost." height={250}>
      <text x="24" y="30" fontWeight="600">Three ways in. NearHome keeps the lowest access cost.</text>
      <circle cx="46" cy="140" r="16" fill={pine} />
      <text x="46" y="144" textAnchor="middle" fill="#fffdf5" fontSize="11" fontWeight="600">Home</text>
      {rows.map((row) => {
        const w = 108;
        return (
          <g key={row.y}>
            <path d={`M 62 140 C 82 140 80 ${row.y} 100 ${row.y}`} fill="none" stroke={row.color} strokeWidth="2.5" />
            {row.steps.map((step, i) => (
              <g key={step}>
                <rect x={100 + i * (w + 10)} y={row.y - 15} width={w} height="30" rx="8" fill={i === 0 ? "#eef1e6" : soft} stroke={row.color} strokeWidth="1.5" />
                <text x={100 + i * (w + 10) + w / 2} y={row.y + 5} textAnchor="middle" fontSize="12">{step}</text>
              </g>
            ))}
            <text x={100} y={row.y - 22} fill={muted} fontSize="12">{row.label}</text>
          </g>
        );
      })}
    </Frame>
  );
}

function BusOverlap() {
  const xs = Array.from({ length: 10 }, (_, i) => 40 + i * 38);
  const row = (y: number, shared: number, label: string, color: string) => (
    <g>
      <text x="24" y={y - 14} fontSize="12" fill={muted}>{label}</text>
      <line x1={xs[0]} y1={y} x2={xs[9]} y2={y} stroke={soft} strokeWidth="8" strokeLinecap="round" />
      {xs.map((x, i) => <circle key={x} cx={x} cy={y} r="6.5" fill={i < shared ? pine : paper} stroke={color} strokeWidth="2" />)}
    </g>
  );
  return (
    <Frame title="When two bus services count as one corridor" desc="If a service shares 70% or more of the shorter route's downstream stops, in the same order, it is grouped into the same corridor. At 30% shared it is a separate corridor." height={300}>
      <text x="24" y="30" fontWeight="600">Do the two routes run along the same stops?</text>
      <text x="24" y="62" fontWeight="600" fill={pine}>Bus 190 compared with Bus 960</text>
      {row(94, 10, "Bus 190: 10 stops", pine)}
      {row(134, 8, "Bus 960: first 8 stops shared (filled)", teal)}
      <text x="440" y="116" fill={pine} fontWeight="600">8 of 10 shared = 80%</text>
      <text x="440" y="136" fill={pine}>70% or more, so one corridor</text>
      <line x1="24" y1="160" x2="616" y2="160" stroke="#e1e4d8" />
      <text x="24" y="190" fontWeight="600" fill={rust}>Bus 190 compared with Bus 5</text>
      {row(222, 10, "Bus 190: 10 stops", pine)}
      {row(262, 3, "Bus 5: only the first 3 stops shared (filled)", amber)}
      <text x="440" y="244" fill={rust} fontWeight="600">3 of 10 shared = 30%</text>
      <text x="440" y="264" fill={rust}>under 70%, so two corridors</text>
    </Frame>
  );
}

function RailMap() {
  const st = (x: number, y: number, fill: string, stroke: string, dashed = false) => (
    <circle key={`${x}-${y}`} cx={x} cy={y} r="7" fill={fill} stroke={stroke} strokeWidth="2.5" strokeDasharray={dashed ? "3 2" : undefined} />
  );
  const line1 = [50, 110, 230, 290, 410, 470, 530];
  return (
    <Frame title="A mini rail map grouped by transfers" desc="Stations on the same line as the one you enter need no transfer. Stations on a line you reach by changing once are one-transfer stations. Stations on a further line need two or more. Stations only reachable in 31 to 45 minutes are counted as extra." height={338}>
      <text x="24" y="30" fontWeight="600">Every station is grouped by the train changes it needs</text>
      <line x1="40" y1="170" x2="540" y2="170" stroke={pine} strokeWidth="7" strokeLinecap="round" />
      <line x1="350" y1="62" x2="350" y2="282" stroke={teal} strokeWidth="7" strokeLinecap="round" />
      <line x1="350" y1="62" x2="560" y2="62" stroke="#c98a14" strokeWidth="7" strokeLinecap="round" />
      {line1.map((x) => st(x, 170, pine, "#fff"))}
      {[100, 135, 205, 240].map((y) => st(350, y, teal, "#fff"))}
      {st(350, 282, paper, teal, true)}
      {[410, 470].map((x) => st(x, 62, "#c98a14", "#fff"))}
      {st(540, 62, paper, "#c98a14", true)}
      <circle cx="170" cy="170" r="12" fill={amber} stroke={pine} strokeWidth="3" />
      <text x="170" y="204" textAnchor="middle" fontSize="12" fontWeight="600">Your station</text>
      <circle cx="350" cy="170" r="11" fill={paper} stroke={rust} strokeWidth="3.5" />
      <text x="372" y="196" fontSize="12" fontWeight="600" fill={rust}>Change here</text>
      <circle cx="350" cy="62" r="11" fill={paper} stroke={rust} strokeWidth="3.5" />
      <text x="338" y="52" textAnchor="end" fontSize="12" fontWeight="600" fill={rust}>Change again</text>
      <text x="60" y="150" fontSize="12" fontWeight="600" fill={pine}>Same line: no transfer</text>
      <text x="368" y="240" fontSize="12" fontWeight="600" fill={teal}>One transfer</text>
      <text x="440" y="96" fontSize="12" fontWeight="600" fill="#8a5a0c">Two or more</text>
      <g fontSize="12">
        <circle cx="34" cy="312" r="6" fill={pine} /><text x="46" y="316">No transfer · 35%</text>
        <circle cx="190" cy="312" r="6" fill={teal} /><text x="202" y="316">One transfer · 35%</text>
        <circle cx="362" cy="312" r="6" fill="#c98a14" /><text x="374" y="316">Two or more · 10%</text>
        <circle cx="520" cy="312" r="6" fill={paper} stroke={muted} strokeWidth="2" strokeDasharray="3 2" /><text x="532" y="316">31–45 min · 20%</text>
      </g>
    </Frame>
  );
}

function PriceRange() {
  const w = 480;
  const lo = 740000;
  const hi = 925000;
  const x = (value: number) => 24 + ((value - lo) / (hi - lo)) * (w - 48);
  const estimate = 832600;
  const asking = 838000;
  const edge = (pct: number) => estimate * (1 + pct / 100);
  const zones = [
    { from: lo, to: edge(-10), fill: "#f3d3c8" },
    { from: edge(-10), to: edge(-5), fill: "#f6e9cc" },
    { from: edge(-5), to: edge(-2), fill: "#e9eede" },
    { from: edge(-2), to: edge(2), fill: soft },
    { from: edge(2), to: edge(5), fill: "#e9eede" },
    { from: edge(5), to: edge(10), fill: "#f6e9cc" },
    { from: edge(10), to: hi, fill: "#f3d3c8" },
  ];
  const comparables = [775000, 790000, 808000, 875000, 915000];
  const legend = [
    { fill: soft, text: "Within 2%: close to estimated market value" },
    { fill: "#e9eede", text: "2% to 5%: slightly above or below" },
    { fill: "#f6e9cc", text: "5% to 10%: above or below" },
    { fill: "#f3d3c8", text: "Over 10%: significantly above or below" },
  ];
  return (
    <Frame title="Where an asking price sits against the estimate" desc="A price line with the estimate, the likely range, the asking price, five comparable sales and the shaded zones used for the price labels." width={w} height={372}>
      <text x="24" y="26" fontSize="14" fontWeight="600">One flat on the price line</text>
      <text x={x(estimate)} y="56" textAnchor="middle" fontSize="13" fontWeight="600" fill={pine}>Estimate $832,600</text>
      {zones.map((zone) => <rect key={zone.from} x={x(zone.from)} y="66" width={x(zone.to) - x(zone.from)} height="30" fill={zone.fill} />)}
      <rect x={x(805000)} y="104" width={x(862000) - x(805000)} height="10" rx="5" fill={teal} opacity="0.75" />
      <text x={x(805000)} y="134" fontSize="13" fill={ink}>Likely range (an example)</text>
      <line x1={x(estimate)} y1="60" x2={x(estimate)} y2="116" stroke={pine} strokeWidth="3" />
      <line x1="24" y1="150" x2={w - 24} y2="150" stroke="#b9bfaf" strokeWidth="2" />
      {[750000, 800000, 850000, 900000].map((tick) => (
        <g key={tick}>
          <line x1={x(tick)} y1="146" x2={x(tick)} y2="154" stroke="#b9bfaf" strokeWidth="2" />
          <text x={x(tick)} y="170" textAnchor="middle" fontSize="12" fill={muted}>{`$${tick / 1000}k`}</text>
        </g>
      ))}
      <path d={`M ${x(asking)} 154 l -8 14 l 16 0 z`} fill={rust} />
      <text x={x(asking) + 12} y="196" fontSize="13" fontWeight="600" fill={rust}>Asking $838,000, +0.6%</text>
      <text x="24" y="226" fontSize="13" fill={ink}>Recent comparable sales</text>
      {comparables.map((value) => <circle key={value} cx={x(value)} cy="244" r="6.5" fill={paper} stroke={teal} strokeWidth="3" />)}
      {legend.map((item, index) => (
        <g key={item.text}>
          <rect x="24" y={274 + index * 22} width="16" height="14" rx="3" fill={item.fill} stroke="#d3d8cd" />
          <text x="48" y={286 + index * 22} fontSize="13">{item.text}</text>
        </g>
      ))}
    </Frame>
  );
}

function MajorRoadEntry() {
  return (
    <Frame title="A real entry point versus a road that only looks close" desc="A car can join a major road at an entry junction. A major road that only passes overhead or beside the flat, with no way on, does not count." height={250}>
      <text x="24" y="30" fontWeight="600">Only a legal way on counts</text>
      <rect x="24" y="70" width="592" height="30" fill={soft} />
      <text x="320" y="90" textAnchor="middle" fill={pine} fontWeight="600">Major road (for example PIE)</text>
      <circle cx="90" cy="190" r="14" fill={pine} />
      <text x="90" y="194" textAnchor="middle" fill="#fffdf5" fontSize="11" fontWeight="600">Home</text>
      <path d="M 104 190 L 250 190 L 250 150 C 250 120 300 110 330 100" fill="none" stroke={pine} strokeWidth="4" />
      <circle cx="330" cy="100" r="9" fill={amber} stroke={pine} strokeWidth="2" />
      <text x="250" y="212" textAnchor="middle" fill={pine} fontWeight="600">Entry point: 6 min drive, counts</text>
      <line x1="470" y1="190" x2="470" y2="100" stroke="#b9bfaf" strokeWidth="4" strokeDasharray="6 5" />
      <circle cx="470" cy="190" r="10" fill="#b9bfaf" />
      <text x="470" y="140" textAnchor="middle" fill={rust} fontSize="22" fontWeight="600">×</text>
      <text x="520" y="212" textAnchor="middle" fill={rust}>Close on a map, no way on</text>
    </Frame>
  );
}

function Independence() {
  const rows = [
    { y: 70, label: "Main route", note: "Road A, Road B, Road C", color: pine, share: "Reference" },
    { y: 130, label: "Independent", note: "Shares 30% or less of road names", color: teal, share: "Road D, Road E, Road C" },
    { y: 190, label: "Partly independent", note: "Shares 31% to 70% of road names", color: amber, share: "Road A, Road B, Road F" },
  ];
  return (
    <Frame title="Independent and partly independent routes" desc="A route that shares 30% or less of its road names with the main route is independent. One that shares 31 to 70% is partly independent." height={236}>
      <text x="24" y="30" fontWeight="600">Routes are compared by the road names they share</text>
      {rows.map((row) => (
        <g key={row.label}>
          <text x="24" y={row.y + 4} fontWeight="600" fill={row.color === amber ? "#8a5a0c" : row.color}>{row.label}</text>
          <line x1="190" y1={row.y} x2="440" y2={row.y} stroke={row.color} strokeWidth="6" strokeLinecap="round" />
          <circle cx="190" cy={row.y} r="7" fill={paper} stroke={row.color} strokeWidth="3" />
          <circle cx="440" cy={row.y} r="7" fill={row.color} />
          <text x="456" y={row.y - 2} fontSize="12">{row.share === "Reference" ? row.note : row.share}</text>
          {row.share !== "Reference" && <text x="456" y={row.y + 14} fontSize="11" fill={muted}>{row.note}</text>}
        </g>
      ))}
    </Frame>
  );
}

function PeakPenalty() {
  return (
    <Frame title="Peak penalty" desc="The same route takes 5 minutes at 10pm and 8 minutes at 8am. The 3 extra minutes are the peak penalty." height={190}>
      <text x="24" y="30" fontWeight="600">The same route, two times of day</text>
      <text x="24" y="76">10pm</text>
      <rect x="84" y="58" width="250" height="26" rx="6" fill={soft} />
      <text x="344" y="76" fontWeight="600">5 min</text>
      <text x="24" y="126">8am</text>
      <rect x="84" y="108" width="400" height="26" rx="6" fill={pine} />
      <rect x="334" y="108" width="150" height="26" rx="6" fill={amber} />
      <text x="494" y="126" fontWeight="600">8 min</text>
      <text x="409" y="152" textAnchor="middle" fill="#8a5a0c" fontWeight="600">+3 min peak penalty</text>
    </Frame>
  );
}

function SchoolRings() {
  const dots = [
    { x: 300, y: 118, near: true },
    { x: 360, y: 150, near: false },
    { x: 238, y: 168, near: false },
    { x: 410, y: 118, near: false },
    { x: 200, y: 108, near: false },
  ];
  return (
    <Frame title="School distance rings" desc="Schools within 1 km count for 15 points each. Schools between 1 and 2 km count for 5 points each." height={290}>
      <text x="24" y="30" fontWeight="600">Distance rings from the flat</text>
      <circle cx="320" cy="150" r="118" fill="#eef1e6" stroke={teal} strokeWidth="2" strokeDasharray="6 5" />
      <circle cx="320" cy="150" r="58" fill={soft} stroke={pine} strokeWidth="2" />
      <circle cx="320" cy="150" r="9" fill={pine} />
      <text x="320" y="176" textAnchor="middle" fontSize="12" fontWeight="600">Flat</text>
      {dots.map((d, i) => <rect key={i} x={d.x - 6} y={d.y - 6} width="12" height="12" rx="2" fill={d.near ? pine : teal} />)}
      <text x="320" y="86" textAnchor="middle" fill={pine} fontSize="12" fontWeight="600">1 km</text>
      <text x="320" y="52" textAnchor="middle" fill={teal} fontSize="12" fontWeight="600">2 km</text>
      <text x="470" y="108" fontWeight="600" fill={pine}>Within 1 km</text>
      <text x="470" y="126" fontSize="12">15 points per school</text>
      <text x="470" y="162" fontWeight="600" fill={teal}>1 to 2 km</text>
      <text x="470" y="180" fontSize="12">5 points per school</text>
      <text x="24" y="278" fill={muted} fontSize="12">Distance is in a straight line, not a walking route.</text>
    </Frame>
  );
}

const diagrams: Record<DiagramId, () => React.JSX.Element> = {
  "access-routes": AccessRoutes,
  "bus-overlap": BusOverlap,
  "rail-map": RailMap,
  "major-road-entry": MajorRoadEntry,
  independence: Independence,
  "peak-penalty": PeakPenalty,
  "school-rings": SchoolRings,
  "price-range": PriceRange,
};

export function MethodologyDiagram({ id }: { id: DiagramId }) {
  const Diagram = diagrams[id];
  return <Diagram />;
}

export function OverallFitFlow({ steps }: { steps: Array<{ title: string; detail: string }> }) {
  const boxH = 48;
  const gap = 24;
  const top = 18;
  const height = top * 2 + steps.length * boxH + (steps.length - 1) * gap;
  return (
    <Frame title="From evidence to your ranking" desc={`The steps are: ${steps.map((step) => step.title).join(", then ")}.`} width={420} height={height}>
      {steps.map((step, index) => {
        const y = top + index * (boxH + gap);
        const yours = index >= 3;
        return (
          <g key={step.title}>
            <rect x="24" y={y} width="372" height={boxH} rx="10" fill={yours ? "#faeeda" : soft} stroke={yours ? "#b8861f" : pine} strokeWidth="1.5" />
            <circle cx="52" cy={y + boxH / 2} r="13" fill={yours ? "#b8861f" : pine} />
            <text x="52" y={y + boxH / 2 + 5} textAnchor="middle" fill="#fffdf5" fontSize="13" fontWeight="600">{index + 1}</text>
            <text x="78" y={y + 21} fontSize="15" fontWeight="600" fill={pine}>{step.title}</text>
            <text x="78" y={y + 39} fontSize="12.5" fill={muted}>{step.detail}</text>
            {index < steps.length - 1 && <path d={`M 210 ${y + boxH + 4} l -7 0 l 7 14 l 7 -14 z`} fill="#8fa595" />}
          </g>
        );
      })}
    </Frame>
  );
}

type CurveKind = "affordability" | "space" | "lease" | "journey" | "fair-price";
type Curve = {
  domain: [number, number];
  ticks: number[];
  lines: Array<{ points: Array<[number, number]>; dash?: string; color: string; label?: string }>;
  sample?: [number, number];
  xLabel: string;
};

const curves: Record<CurveKind, Curve> = {
  affordability: { domain: [50, 130], ticks: [50, 75, 100, 125], lines: [{ points: [[50, 100], [75, 100], [125, 0], [130, 0]], color: pine }], sample: [93.1, 63.8], xLabel: "Asking price, % of budget" },
  space: { domain: [50, 130], ticks: [60, 90, 120], lines: [{ points: [[50, 0], [60, 0], [120, 100], [130, 100]], color: pine }], sample: [112, 86.7], xLabel: "Floor area, sqm" },
  lease: { domain: [30, 90], ticks: [40, 60, 80], lines: [{ points: [[30, 0], [40, 0], [80, 100], [90, 100]], color: pine }], sample: [63, 57.5], xLabel: "Remaining lease, years" },
  journey: { domain: [0, 90], ticks: [15, 45, 75], lines: [{ points: [[0, 100], [15, 100], [75, 0], [90, 0]], color: pine }], xLabel: "Journey time, minutes" },
  "fair-price": {
    domain: [-20, 20],
    ticks: [-20, -10, 0, 10, 20],
    lines: [
      { points: [[-20, 100], [-10, 100], [10, 0], [20, 0]], color: pine, label: "High" },
      { points: [[-20, 100], [-12.5, 100], [12.5, 0], [20, 0]], color: teal, dash: "6 4", label: "Medium" },
      { points: [[-20, 100], [20, 0]], color: "#b8861f", dash: "2 4", label: "Low" },
    ],
    sample: [0.65, 46.8],
    xLabel: "Asking price vs estimate, %",
  },
};

export function PriorityCurve({ kind, label }: { kind: CurveKind; label: string }) {
  const curve = curves[kind];
  const w = 280;
  const h = 190;
  const left = 52;
  const right = 14;
  const top = 14;
  const bottom = 44;
  const [d0, d1] = curve.domain;
  const px = (value: number) => left + ((value - d0) / (d1 - d0)) * (w - left - right);
  const py = (value: number) => top + (1 - value / 100) * (h - top - bottom);
  return (
    <Frame title={`${label} score curve`} desc={`How the ${label.toLowerCase()} input maps onto a 0 to 100 score.`} width={w} height={h}>
      {[0, 50, 100].map((tick) => (
        <g key={tick}>
          <line x1={left} y1={py(tick)} x2={w - right} y2={py(tick)} stroke="#e1e4d8" />
          <text x={left - 6} y={py(tick) + 4} textAnchor="end" fontSize="11" fill={muted}>{tick}</text>
        </g>
      ))}
      {curve.ticks.map((tick) => (
        <g key={tick}>
          <line x1={px(tick)} y1={py(0)} x2={px(tick)} y2={py(0) + 5} stroke="#b9bfaf" />
          <text x={px(tick)} y={py(0) + 18} textAnchor="middle" fontSize="11" fill={muted}>{tick}</text>
        </g>
      ))}
      <text x={(left + w - right) / 2} y={h - 8} textAnchor="middle" fontSize="11.5" fill={ink}>{curve.xLabel}</text>
      <text transform={`rotate(-90 12 ${(top + h - bottom) / 2})`} x="12" y={(top + h - bottom) / 2 + 4} textAnchor="middle" fontSize="11.5" fill={ink}>Score</text>
      {curve.lines.map((line, index) => (
        <polyline key={index} points={line.points.map(([x, y]) => `${px(x)},${py(y)}`).join(" ")} fill="none" stroke={line.color} strokeWidth="3" strokeDasharray={line.dash} strokeLinejoin="round" strokeLinecap="round" />
      ))}
      {curve.lines.length > 1 && (
        <g fontSize="11" fill={ink}>
          {curve.lines.map((line, index) => (
            <g key={line.label}>
              <line x1={w - right - 86} y1={22 + index * 15} x2={w - right - 66} y2={22 + index * 15} stroke={line.color} strokeWidth="3" strokeDasharray={line.dash} />
              <text x={w - right - 60} y={26 + index * 15}>{line.label}</text>
            </g>
          ))}
        </g>
      )}
      {curve.sample && (
        <g>
          <circle cx={px(curve.sample[0])} cy={py(curve.sample[1])} r="6" fill={amber} stroke={pine} strokeWidth="2.5" />
        </g>
      )}
    </Frame>
  );
}

// ============================================================================
// Domain types
// ============================================================================

// The engines are a closed set. Every place an id is used — data lookups,
// tab wiring, DOM ids — is checked against this union, so a typo like
// "glm_ocr" instead of "glmocr" is a compile error, not a silent `undefined`
// at 2am during a demo. This stays a strict union on purpose (see "Why
// TypeScript, Here" #4) — to add a model, add its id HERE first, then add
// the matching entry to data.ts's MODEL_META. Forget this step and tsc
// rejects the new entry in data.ts with exactly which id is unrecognized.
type ModelId = "lighton" | "datalab" | "paddleocr" | "fastocr" | "baseerocr" | "glmocr";

type ModelFlag = "low-quality" | "anomaly";

// The real pricing data (models/cost.md) is NOT one shape. It's three
// different pricing models mashed into one markdown table:
//   - a metered per-page rate (a range, since vendors quote low/high)
//   - a flat fixed fee regardless of page count (FastOCR's manual dashboard run)
//   - a monthly subscription with a page cap (Besser OCR)
// Forcing all three into a single "$/page" number is exactly how the
// original cost.md ended up self-contradictory (see the FastOCR anomaly).
// A discriminated union keeps each shape honest, and the compiler forces
// every consumer to handle all three — including any new shape added later.
type CostTier =
  | { kind: "metered"; low: number; high: number }
  | { kind: "flatFee"; amount: number; forPages: number }
  | { kind: "subscription"; monthlyAmount: number; pageCapPerMonth: number };

// The hand-edited part — see data.ts. Deliberately has NO `text` field:
// OCR output is the actual content of models/<file>, so it's read and
// cleaned by the build script rather than pasted into TypeScript source.
interface ModelMeta {
  id: ModelId;
  name: string;
  engine: string;
  file: string; // filename inside models/
  flags: ModelFlag[];
  cost: CostTier; // the comparable, hosted/managed-tier figure used in the chart
  costQuote: string;
  actualRunCost?: CostTier; // a real one-off figure for *this* document, when known
}

interface ModelResult extends ModelMeta {
  text: string;
}

interface PageImage {
  n: number;
  thumb: string;
  full: string;
}

interface DemoData {
  models: ModelResult[];
  pages: PageImage[];
}

// MODEL_META is real, compiled TypeScript defined in data.ts (compiled
// together with this file, so it's globally visible with no import needed —
// see the `const MODEL_META: ModelMeta[]` there).
// MODEL_TEXT and PAGES are supplied by the build script: MODEL_TEXT from
// reading + cleaning each MODEL_META entry's `file` out of models/, and
// PAGES from rendering the source PDF. Neither belongs in hand-edited
// source — one is derived, the other is binary image data.
declare const MODEL_TEXT: Record<string, string>;
declare const PAGES: PageImage[];

const DATA: DemoData = {
  models: MODEL_META.map((m) => ({ ...m, text: MODEL_TEXT[m.id] ?? "" })),
  pages: PAGES,
};

// ============================================================================
// Exhaustiveness — the actual TypeScript "superpower" for this case
// ============================================================================

// If a fourth CostTier variant is ever added and a switch below forgets to
// handle it, `assertNever` makes that a compile error at every call site,
// not a silent NaN in production.
function assertNever(x: never): never {
  throw new Error("Unhandled case: " + JSON.stringify(x));
}

const CHART_PAGE_BASIS = 50_000; // matches models/cost.md's own "Total Cost for 50k Pages" framing

// Normalizes any pricing shape to a single comparable $-per-50,000-pages
// figure for the chart. This is the one function allowed to collapse the
// union to a number — everywhere else keeps the real shape (see
// costRangeLabel, which reports each tier in its own native unit).
function costToChartBasis(tier: CostTier): number {
  switch (tier.kind) {
    case "metered":
      return ((tier.low + tier.high) / 2 / 1000) * CHART_PAGE_BASIS;
    case "flatFee":
      return (tier.amount / tier.forPages) * CHART_PAGE_BASIS;
    case "subscription":
      return (tier.monthlyAmount / tier.pageCapPerMonth) * CHART_PAGE_BASIS;
    default:
      return assertNever(tier);
  }
}

function costRangeLabel(tier: CostTier): string {
  switch (tier.kind) {
    case "metered":
      return tier.low === tier.high
        ? `$${tier.low.toFixed(2)}/1k (flat rate)`
        : `$${tier.low.toFixed(2)}–$${tier.high.toFixed(2)}/1k`;
    case "flatFee":
      return `$${tier.amount.toFixed(2)} flat for ${tier.forPages} page${tier.forPages === 1 ? "" : "s"}`;
    case "subscription":
      return `$${tier.monthlyAmount.toFixed(2)}/mo, cap ${tier.pageCapPerMonth.toLocaleString()} pages`;
    default:
      return assertNever(tier);
  }
}

function costKindLabel(tier: CostTier): string {
  switch (tier.kind) {
    case "metered": return "Metered API";
    case "flatFee": return "Flat fee";
    case "subscription": return "Subscription";
    default: return assertNever(tier);
  }
}

// ============================================================================
// Small typed DOM helpers — strict null checks catch a missing element at
// compile time (well, at first run against real markup) instead of a
// "Cannot read properties of null" three interactions later.
// ============================================================================

function must<T extends Element = Element>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`#${id} is missing from the DOM`);
  return el as unknown as T;
}

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  html?: string
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (html !== undefined) node.innerHTML = html;
  return node;
}

// A tiny generic tab component: T just needs an id, so it works for models,
// or for anything else with the same shape, without rewriting the wiring.
function createTabGroup<T extends { id: string }>(
  tabsHost: HTMLElement,
  panelsHost: HTMLElement,
  items: T[],
  renderTabLabel: (item: T) => string,
  renderPanel: (item: T) => HTMLElement
): void {
  items.forEach((item, i) => {
    const tab = el("button", "tab" + (i === 0 ? " active" : ""), renderTabLabel(item));
    tab.dataset.id = item.id;
    tab.addEventListener("click", () => activate(item.id));
    tabsHost.appendChild(tab);

    const panel = renderPanel(item);
    panel.classList.add("panel");
    if (i === 0) panel.classList.add("active");
    panel.dataset.id = item.id;
    panelsHost.appendChild(panel);
  });

  function activate(id: string): void {
    tabsHost.querySelectorAll<HTMLElement>(".tab").forEach((t) => t.classList.toggle("active", t.dataset.id === id));
    panelsHost.querySelectorAll<HTMLElement>(".panel").forEach((p) => p.classList.toggle("active", p.dataset.id === id));
  }
}

// ============================================================================
// Theme engine
// ============================================================================
// Three visual identities, each with a light and dark variant. Modeled as a
// `Record<ThemeName, Record<ThemeMode, ThemeTokens>>` rather than a switch of
// string literals — TypeScript's mapped-object checking means leaving out a
// theme, or a mode within a theme, or a single token within a mode, is a
// compile error. There is no runtime code path where "blueprint dark" can
// silently fall back to undefined colors; it either exists at compile time
// or the build fails.

type ThemeName = "manuscript" | "blueprint" | "terminal";
type ThemeMode = "light" | "dark";

interface ThemeTokens {
  paper: string;
  paper2: string;
  paper3: string;
  ink: string;
  inkSoft: string;
  inkFaint: string;
  accent1: string; // primary accent (bar-chart hue, resizer grip)
  accent1Strong: string; // emphasis variant for headings/active-state text
  accent2: string; // secondary accent (links, tabs)
  status: string;  // reserved: warnings/flags only, never decorative
  line: string;
  lineStrong: string;
  ramp: readonly [string, string, string, string, string, string];
  fontDisplay: string;
}

const THEMES: Record<ThemeName, Record<ThemeMode, ThemeTokens>> = {
  manuscript: {
    light: {
      paper: "#eae2cc", paper2: "#f4efe0", paper3: "#ddd2ae",
      ink: "#241f16", inkSoft: "#5f5645", inkFaint: "#8a8069",
      accent1: "#a97b2f", accent1Strong: "#7a5511", accent2: "#1f4e63", status: "#9c3b2e",
      line: "#cabf9c", lineStrong: "#b0a37a",
      ramp: ["#ecd9a8", "#ddbf7c", "#c99f52", "#b4842f", "#966816", "#7a5511"],
      fontDisplay: '"Fraunces Display", Georgia, "Iowan Old Style", serif',
    },
    dark: {
      paper: "#1b1712", paper2: "#221e17", paper3: "#2c261c",
      ink: "#ece3cc", inkSoft: "#bcb195", inkFaint: "#8a8069",
      accent1: "#d1a44e", accent1Strong: "#e8c079", accent2: "#7bb6d1", status: "#e08a72",
      line: "#3c3527", lineStrong: "#4d442f",
      ramp: ["#4a3d20", "#675328", "#866c30", "#a98639", "#cca24a", "#eec573"],
      fontDisplay: '"Fraunces Display", Georgia, "Iowan Old Style", serif',
    },
  },
  blueprint: {
    light: {
      paper: "#dbe6f0", paper2: "#eaf1f8", paper3: "#c7d7e6",
      ink: "#0f2438", inkSoft: "#3c5872", inkFaint: "#7996ac",
      accent1: "#c9781f", accent1Strong: "#8f550f", accent2: "#1f6fa8", status: "#b5432e",
      line: "#b7c9da", lineStrong: "#9bb3c9",
      ramp: ["#cfe4f5", "#a9cdec", "#7bb0dd", "#4f8fc9", "#2f6fac", "#1a4f85"],
      fontDisplay: '"Fraunces Sub", -apple-system, "Segoe UI", sans-serif',
    },
    dark: {
      paper: "#0b2a4a", paper2: "#0f3559", paper3: "#154572",
      ink: "#eaf3ff", inkSoft: "#b9d3ea", inkFaint: "#7fa2c2",
      accent1: "#f2a65a", accent1Strong: "#ffc98a", accent2: "#6fc3ff", status: "#ff8f73",
      line: "#1f4d78", lineStrong: "#2c5f8f",
      ramp: ["#123a5c", "#1a4d78", "#256792", "#3684ab", "#59a8cc", "#8ecdea"],
      fontDisplay: '"Fraunces Sub", -apple-system, "Segoe UI", sans-serif',
    },
  },
  terminal: {
    light: {
      paper: "#f4f6f1", paper2: "#ffffff", paper3: "#e6ebe0",
      ink: "#16241a", inkSoft: "#3f5c46", inkFaint: "#7a8f7c",
      accent1: "#b6790a", accent1Strong: "#8a5c08", accent2: "#0f8a4c", status: "#c23b3b",
      line: "#d7e0d2", lineStrong: "#c0cdb9",
      ramp: ["#cdead0", "#a3d6a8", "#79c081", "#4fa85c", "#2f8a3f", "#1c6b2c"],
      fontDisplay: 'ui-monospace, "SF Mono", Menlo, monospace',
    },
    dark: {
      paper: "#0c0e0c", paper2: "#12140f", paper3: "#1a1d16",
      ink: "#c9f2c0", inkSoft: "#8fbf86", inkFaint: "#5b7a55",
      accent1: "#ffcf6b", accent1Strong: "#ffe19a", accent2: "#7bffb0", status: "#ff6b6b",
      line: "#20281c", lineStrong: "#2c3824",
      ramp: ["#163a1c", "#1f5228", "#2c6d34", "#3f8f44", "#63b95c", "#93e08a"],
      fontDisplay: 'ui-monospace, "SF Mono", Menlo, monospace',
    },
  },
};

function applyTheme(name: ThemeName, mode: ThemeMode): void {
  const tokens = THEMES[name][mode];
  const root = document.documentElement;
  root.style.setProperty("--paper", tokens.paper);
  root.style.setProperty("--paper-2", tokens.paper2);
  root.style.setProperty("--paper-3", tokens.paper3);
  root.style.setProperty("--ink", tokens.ink);
  root.style.setProperty("--ink-soft", tokens.inkSoft);
  root.style.setProperty("--ink-faint", tokens.inkFaint);
  root.style.setProperty("--gold", tokens.accent1);
  root.style.setProperty("--gold-strong", tokens.accent1Strong);
  root.style.setProperty("--lapis", tokens.accent2);
  root.style.setProperty("--vermilion", tokens.status);
  root.style.setProperty("--line", tokens.line);
  root.style.setProperty("--line-strong", tokens.lineStrong);
  root.style.setProperty("--font-display", tokens.fontDisplay);
  tokens.ramp.forEach((c, i) => root.style.setProperty(`--ramp-${i + 1}`, c));

  // Code blocks always read as a dark console pane, regardless of the
  // active light/dark mode — pulled from this theme's *dark* variant so
  // each family (manuscript/blueprint/terminal) still gets its own tint.
  const darkTokens = THEMES[name].dark;
  root.style.setProperty("--code-bg", darkTokens.paper2);
  root.style.setProperty("--code-fg", darkTokens.ink);

  root.dataset.themeName = name;
  root.style.colorScheme = mode;
}

function detectSystemMode(): ThemeMode {
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

(function initTheme(): void {
  let themeName: ThemeName = "manuscript";
  let mode: ThemeMode = detectSystemMode();

  applyTheme(themeName, mode);

  const modeBtn = must<HTMLButtonElement>("themeToggle");
  modeBtn.addEventListener("click", () => {
    mode = mode === "dark" ? "light" : "dark";
    applyTheme(themeName, mode);
  });

  const swatches = must<HTMLElement>("themeSwatches");
  swatches.querySelectorAll<HTMLButtonElement>("button").forEach((btn) => {
    btn.addEventListener("click", () => {
      const name = btn.dataset.theme as ThemeName;
      themeName = name;
      applyTheme(themeName, mode);
      swatches.querySelectorAll("button").forEach((b) => b.classList.toggle("active", b === btn));
      btn.classList.add("pulse");
      btn.addEventListener("animationend", () => btn.classList.remove("pulse"), { once: true });
    });
  });
})();

// ============================================================================
// Manuscript rail + lightbox
// ============================================================================

(function initGallery(): void {
  const rail = must<HTMLElement>("pageRail");
  const lb = must<HTMLElement>("lightbox");
  const lbImg = must<HTMLImageElement>("lbImg");
  let idx = 0;

  DATA.pages.forEach((p, i) => {
    const card = el("div", "page-card", `<img src="data:image/jpeg;base64,${p.thumb}" alt="Page ${p.n}" loading="lazy" /><div class="num">Page ${p.n}</div>`);
    card.addEventListener("click", () => open(i));
    rail.appendChild(card);
  });

  function open(i: number): void {
    idx = i;
    render();
    lb.classList.add("open");
  }
  function render(): void {
    lbImg.src = `data:image/jpeg;base64,${DATA.pages[idx]!.full}`;
    lbImg.style.animation = "none";
    void lbImg.offsetWidth;
    lbImg.style.animation = "";
  }
  must<HTMLButtonElement>("lbClose").addEventListener("click", () => lb.classList.remove("open"));
  must<HTMLButtonElement>("lbPrev").addEventListener("click", () => {
    idx = (idx - 1 + DATA.pages.length) % DATA.pages.length;
    render();
  });
  must<HTMLButtonElement>("lbNext").addEventListener("click", () => {
    idx = (idx + 1) % DATA.pages.length;
    render();
  });
  lb.addEventListener("click", (e) => {
    if (e.target === lb) lb.classList.remove("open");
  });
  document.addEventListener("keydown", (e) => {
    if (!lb.classList.contains("open")) return;
    if (e.key === "Escape") lb.classList.remove("open");
    if (e.key === "ArrowLeft") must<HTMLButtonElement>("lbPrev").click();
    if (e.key === "ArrowRight") must<HTMLButtonElement>("lbNext").click();
  });
})();

// ============================================================================
// Resizable pages/results split
// ============================================================================

(function initResizer(): void {
  const layout = must<HTMLElement>("compareLayout");
  const resizer = must<HTMLElement>("resizer");
  const presets = must<HTMLElement>("resizePresets");
  const MIN = 100, MAX = 560;

  function setWidth(px: number, animate: boolean): void {
    const clamped = Math.max(MIN, Math.min(MAX, px));
    layout.classList.toggle("dragging", !animate);
    layout.style.setProperty("--rail-w", clamped + "px");
    presets.querySelectorAll<HTMLButtonElement>("button").forEach((b) => {
      b.classList.toggle("active", Math.abs(Number(b.dataset.w) - clamped) < 15);
    });
  }

  presets.addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!btn) return;
    setWidth(Number(btn.dataset.w), true);
  });

  let dragging = false;
  resizer.addEventListener("pointerdown", (e) => {
    dragging = true;
    resizer.setPointerCapture(e.pointerId);
    layout.classList.add("dragging");
  });
  resizer.addEventListener("pointermove", (e) => {
    if (!dragging) return;
    const rect = layout.getBoundingClientRect();
    setWidth(e.clientX - rect.left, false);
  });
  function endDrag(): void {
    dragging = false;
    layout.classList.remove("dragging");
  }
  resizer.addEventListener("pointerup", endDrag);
  resizer.addEventListener("pointercancel", endDrag);
  resizer.addEventListener("dblclick", () => setWidth(220, true));
  resizer.addEventListener("keydown", (e) => {
    const cur = parseFloat(getComputedStyle(layout).getPropertyValue("--rail-w")) || 220;
    if (e.key === "ArrowLeft") { setWidth(cur - 20, true); e.preventDefault(); }
    if (e.key === "ArrowRight") { setWidth(cur + 20, true); e.preventDefault(); }
    if (e.key === "Home") { setWidth(MIN, true); e.preventDefault(); }
    if (e.key === "End") { setWidth(MAX, true); e.preventDefault(); }
  });
})();

// ============================================================================
// Cost dot plot + table — both driven entirely through costToChartBasis /
// costRangeLabel, so the chart and the table are guaranteed to agree (no
// separate "chart number" vs "table number" that can silently drift apart,
// which is exactly what happened across the plain-JS cost.md / fastocr.md
// files this data was extracted from).
// ============================================================================

// Small, boring, and reused only here: rounds an axis max up to a "nice"
// step count so gridlines land on 0 / 500 / 1000 / ... instead of whatever
// the data happens to produce.
function niceTicks(max: number, targetCount = 4): number[] {
  if (max <= 0) return [0];
  const rawStep = max / targetCount;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const residual = rawStep / magnitude;
  const niceResidual = residual >= 5 ? 10 : residual >= 2 ? 5 : residual >= 1 ? 2 : 1;
  const step = niceResidual * magnitude;
  // Keep adding ticks until one actually reaches max — a fixed iteration
  // count can stop one step short (e.g. max=1650, step=500 → last tick
  // 1500 < max), which would silently clip the highest dot to 100%.
  const ticks: number[] = [0];
  while (ticks[ticks.length - 1]! < max) {
    ticks.push(Math.round((ticks[ticks.length - 1]! + step) * 100) / 100);
  }
  return ticks;
}

function formatUSD(n: number): string {
  return "$" + n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function buildBarChart(models: ModelResult[], values: number[], cheapestValue: number): HTMLElement {
  const ramp = ["--ramp-1", "--ramp-2", "--ramp-3", "--ramp-4", "--ramp-5", "--ramp-6"];
  const max = Math.max(...values);
  const chart = el("div", "chart");
  models.forEach((m, i) => {
    const value = values[i]!;
    const isCheapest = value === cheapestValue;
    const pct = Math.max(2, (value / max) * 100);
    const row = el("div", "bar-row" + (m.flags.includes("anomaly") ? " flagged" : ""));
    row.innerHTML = `
      <div class="name">${m.name}${isCheapest ? '<span class="cheapest-chip">cheapest</span>' : ""}</div>
      <div class="bar-track"><div class="bar-fill${isCheapest ? " bar-cheapest" : ""}" data-pct="${pct}" style="background: var(${ramp[i]!})"
           title="${costKindLabel(m.cost)}: ${costRangeLabel(m.cost)} → ${formatUSD(value)} per ${CHART_PAGE_BASIS.toLocaleString()} pages"></div></div>
      <div class="value">${formatUSD(value)}</div>
    `;
    chart.appendChild(row);
  });
  return chart;
}

function buildDotPlot(models: ModelResult[], values: number[], cheapestValue: number): HTMLElement {
  const ticks = niceTicks(Math.max(...values), 4);
  const axisMax = ticks[ticks.length - 1]!;

  const plot = el("div", "dotplot");

  const grid = el("div", "dot-grid");
  ticks.forEach((t) => {
    const line = el("div", "dot-grid-line");
    line.style.left = (t / axisMax) * 100 + "%";
    grid.appendChild(line);
  });
  plot.appendChild(grid);

  models.forEach((m, i) => {
    const value = values[i]!;
    const isCheapest = value === cheapestValue;
    const pct = Math.min(100, (value / axisMax) * 100);
    const row = el("div", "dot-row" + (m.flags.includes("anomaly") ? " flagged" : ""));
    row.innerHTML = `
      <div class="name">${m.name}${isCheapest ? '<span class="cheapest-chip">cheapest</span>' : ""}</div>
      <div class="dot-track">
        <div class="dot-line"></div>
        <div class="dot${isCheapest ? " dot-cheapest" : ""}" data-pct="${pct}"
             title="${costKindLabel(m.cost)}: ${costRangeLabel(m.cost)} → ${formatUSD(value)} per ${CHART_PAGE_BASIS.toLocaleString()} pages"></div>
      </div>
      <div class="value">${formatUSD(value)}</div>
    `;
    plot.appendChild(row);
  });

  const axis = el("div", "dot-axis");
  axis.appendChild(el("div")); // spacer, mirrors the name column
  const axisTicks = el("div", "dot-axis-ticks");
  ticks.forEach((t) => {
    axisTicks.appendChild(el("span", "dot-axis-tick", "$" + t.toLocaleString()));
  });
  axis.appendChild(axisTicks);
  axis.appendChild(el("div")); // spacer, mirrors the value column
  plot.appendChild(axis);

  return plot;
}

(function initCost(): void {
  const models = [...DATA.models].sort((a, b) => costToChartBasis(a.cost) - costToChartBasis(b.cost));
  const values = models.map((m) => costToChartBasis(m.cost));
  const cheapestValue = Math.min(...values);

  const captionText = `Sorted cheapest → most expensive, normalized to $ per ${CHART_PAGE_BASIS.toLocaleString()} pages regardless of pricing shape (metered / flat fee / subscription) — hover a mark for the real shape behind the number. To change these figures, edit the \`cost\` field for each model in <code>demo/typescript-src/data.ts</code> and rebuild with <code>python3 demo/build.py</code>.`;

  const barChart = buildBarChart(models, values, cheapestValue);
  const barView = must<HTMLElement>("barView");
  barView.appendChild(barChart);
  barView.appendChild(el("div", "chart-caption", captionText));

  const dotPlot = buildDotPlot(models, values, cheapestValue);
  const dotView = must<HTMLElement>("dotView");
  dotView.appendChild(dotPlot);
  dotView.appendChild(el("div", "chart-caption", captionText));

  // Both charts' fills are revealed together, the first time either becomes
  // visible, by observing the section itself (always in the layout, unlike
  // its currently-hidden sibling view) rather than each chart individually.
  const revealed = { done: false };
  function reveal(): void {
    if (revealed.done) return;
    revealed.done = true;
    barChart.querySelectorAll<HTMLElement>(".bar-fill").forEach((f) => { f.style.width = f.dataset.pct + "%"; });
    dotPlot.querySelectorAll<HTMLElement>(".dot").forEach((d) => { d.style.left = d.dataset.pct + "%"; });
  }
  const costSection = must<HTMLElement>("cost");
  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        reveal();
        io.unobserve(e.target);
      }
    });
  }, { threshold: 0.15 });
  io.observe(costSection);

  const table = el("table", "cost-table", `<thead><tr><th>Engine</th><th>Pricing shape</th><th>Est. $ per ${CHART_PAGE_BASIS.toLocaleString()} pages</th><th>Native rate</th><th>Source note</th></tr></thead>`);
  const tbody = el("tbody");
  models.forEach((m, i) => {
    const tr = el(
      "tr",
      undefined,
      `<td>${m.name}${m.flags.includes("low-quality") ? " ⚠" : ""}${values[i] === cheapestValue ? " 🏆" : ""}</td>
       <td>${costKindLabel(m.cost)}</td>
       <td class="num">${formatUSD(values[i]!)}</td>
       <td>${costRangeLabel(m.cost)}</td>
       <td>${m.costQuote}</td>`
    );
    tbody.appendChild(tr);
  });
  table.appendChild(tbody);
  const tableWrap = el("div", "table-wrap");
  tableWrap.appendChild(table);
  must<HTMLElement>("tableView").appendChild(tableWrap);

  // One callout per model with a real one-off actualRunCost — generated
  // from the data rather than hand-written per model, so a 7th model with
  // its own "quoted rate vs. what I actually paid" story doesn't need a
  // matching hand-written paragraph here to show up.
  const anomalyHost = must<HTMLElement>("anomalyCallouts");
  models
    .filter((m): m is ModelResult & { actualRunCost: CostTier } => m.actualRunCost !== undefined)
    .forEach((m) => {
      const chartFigure = formatUSD(costToChartBasis(m.cost));
      anomalyHost.appendChild(
        el(
          "div",
          "anomaly-callout",
          `<b>Why isn't ${m.name}'s ${costRangeLabel(m.actualRunCost)} in the chart above?</b> That figure is the actual cost of running this specific document through ${m.name} (${costKindLabel(m.actualRunCost).toLowerCase()}) — not a metered per-page rate. The chart instead uses ${m.name}'s ${costKindLabel(m.cost).toLowerCase()} tier (${costRangeLabel(m.cost)} ≈ ${chartFigure} per ${CHART_PAGE_BASIS.toLocaleString()} pages) so all engines are compared on the same basis. See the ${m.name} tab for the full detail.`
        )
      );
    });

  const views: Record<string, HTMLElement> = {
    bar: barView,
    dot: dotView,
    table: must<HTMLElement>("tableView"),
  };
  must<HTMLElement>("viewToggle").addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!btn) return;
    const target = btn.dataset.view ?? "";
    document.querySelectorAll<HTMLButtonElement>("#viewToggle button").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    reveal(); // in case the section never scrolled past the observer threshold
    for (const [key, view] of Object.entries(views)) {
      view.style.display = key === target ? "" : "none";
    }
  });
})();

// ============================================================================
// Markdown-ish -> HTML
// ============================================================================

function escapeHtml(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderText(md: string): string {
  const blocks = md.split(/\n\s*\n/);
  return blocks
    .map((block) => {
      const t = block.trim();
      if (!t) return "";
      if (t === "⟦FIGURE⟧") return `<span class="figure-chip">🖼 figure</span>`;
      if (/^---+$/.test(t)) return "<hr />";
      if (/^#{1,6}\s+/.test(t)) {
        return `<h3 class="doc-heading">${escapeHtml(t.replace(/^#{1,6}\s+/, ""))}</h3>`;
      }
      const html = escapeHtml(t)
        .replace(/⟦FIGURE⟧/g, `</p><span class="figure-chip">🖼 figure</span><p>`)
        .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
        .replace(/\n/g, "<br/>");
      return `<p>${html}</p>`;
    })
    .join("\n");
}

// ============================================================================
// Tabs
// ============================================================================

(function initTabs(): void {
  const tabsHost = must<HTMLElement>("tabs");
  const panelsHost = must<HTMLElement>("panels");

  createTabGroup(
    tabsHost,
    panelsHost,
    DATA.models,
    (m) => m.name + (m.flags.includes("low-quality") ? `<span class="flag-dot" title="Flagged: low quality"></span>` : ""),
    (m) => {
      let flagBanner = "";
      if (m.flags.includes("low-quality")) {
        flagBanner = `<div class="flag-banner">⚠ <span><b>Flagged: low-quality output.</b> This run's text doesn't reliably track the source document — it reads as a garbled or failed OCR pass. Shown as-is rather than hidden, since an honest benchmark includes the misses.</span></div>`;
      }
      if (m.actualRunCost) {
        flagBanner += `<div class="flag-banner">💰 <span><b>Cost anomaly on this run.</b> Running this exact 5-page document cost <b>${costRangeLabel(m.actualRunCost)}</b> (${costKindLabel(m.actualRunCost)}) — the chart above uses ${m.name}'s programmatic API tier instead (${costRangeLabel(m.cost)}) so all six engines compare on the same basis.</span></div>`;
      }
      return el(
        "div",
        undefined,
        `
        <div class="panel-head">
          <h3>${m.name}</h3>
          <span class="engine">${m.engine} · source: <code>${m.file}</code></span>
        </div>
        ${flagBanner}
        <div class="ocr-text">${renderText(m.text)}</div>
      `
      );
    }
  );
})();

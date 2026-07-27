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

// The real pricing data is NOT one shape. The audited price list
// (`OCR pricing - final.csv`) mixes five genuinely different billing models
// into one table:
//   - a metered per-page rate (a range, since vendors quote low/high)
//   - a flat fixed fee regardless of page count (FastOCR's manual dashboard run)
//   - a monthly subscription, capped or uncapped (Besser OCR)
//   - a per-token rate, where cost/page depends on tokens the page happens to emit
//   - a per-compute-second rate, where cost/page depends on how slow the node is
// Forcing all five into a single "$/page" number is exactly how the source
// table ended up self-contradictory (see the FastOCR anomaly, and the 10×/5×
// arithmetic errors the review caught). A discriminated union keeps each shape
// honest, and the compiler forces every consumer to handle all of them.
//
// `perToken` and `computeTime` are not hypothetical: they arrived with the
// audited CSV, and adding them to this union is what surfaced every switch
// that needed a new branch — see "Why TypeScript, here" #2 on the page.
type CostTier =
  | { kind: "metered"; low: number; high: number } // $ per 1,000 pages
  | { kind: "flatFee"; amount: number; forPages: number }
  | { kind: "subscription"; monthlyAmount: number; pageCapPerMonth: number | null }
  | { kind: "perToken"; usdPerMillionTokens: number; tokensPerPage: number }
  | { kind: "computeTime"; usdPerComputeSecond: number; secondsPerPage: number };

// What a price figure is actually worth. The audited CSV's central finding is
// that most of these numbers are not confirmable, so confidence is modeled as
// a first-class closed set rather than a free-text column nobody reads: it
// drives the badge, the sort order, and the "verified only" filter.
type SourceConfidence =
  | "verified" // vendor's own published rate, checked
  | "vendor-email" // confirmed by direct vendor correspondence
  | "derived" // arithmetic from a different billing unit, at an assumed rate
  | "disputed" // a real published rate exists and materially disagrees
  | "unverified"; // no vendor source could be found at all

// One row of the audited price list — see PRICING_ROWS in data.ts. Priced
// independently of whether the engine was ever run: `benchmarked` is the only
// link back to an OCR tab, and it is null for engines listed but not tested.
interface PricingRow {
  model: string;
  provider: string;
  cost: CostTier;
  ceiling: string; // the vendor's own worst-case metric, verbatim
  worstCase: string; // tokens/page or why that's N/A, verbatim
  confidence: SourceConfidence;
  corrected: boolean; // review changed this figure from the original table
  note: string; // the reviewer's finding, verbatim
  benchmarked: ModelId | null;
}

// The hand-edited part — see data.ts. Deliberately has NO `text` field:
// OCR output is the actual content of models/<file>, so it's read and
// cleaned by the build script rather than pasted into TypeScript source.
// Also deliberately has no `cost`: pricing lives in PRICING_ROWS, which is a
// different length and a different shape (12 priced rows, 6 tested engines).
interface ModelMeta {
  id: ModelId;
  name: string;
  engine: string;
  file: string; // filename inside models/
  flags: ModelFlag[];
  note: string; // operational note about the engine, not its price
  actualRunCost?: CostTier; // a real invoice for *this* document, when known
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

// The representative priced row for an engine, for the tabs. PaddleOCR has
// three (token-standard / token-dense / flat-page), so "first match in table
// order" is the documented rule rather than an accident of iteration.
function primaryPricingRow(id: ModelId): PricingRow | undefined {
  return PRICING_ROWS.find((r) => r.benchmarked === id);
}

// ============================================================================
// Exhaustiveness — the actual TypeScript "superpower" for this case
// ============================================================================

// If a fourth CostTier variant is ever added and a switch below forgets to
// handle it, `assertNever` makes that a compile error at every call site,
// not a silent NaN in production.
function assertNever(x: never): never {
  throw new Error("Unhandled case: " + JSON.stringify(x));
}

// The audited CSV prices every row at two volumes, and the gap between them is
// the whole point: eleven rows scale linearly and one (Besser's flat
// subscription) does not, so its effective $/page falls 50× across this range
// while nothing else moves. A single hardcoded basis would hide that entirely.
const VOLUME_BASES = [1_000, 50_000] as const;
type VolumeBasis = (typeof VOLUME_BASES)[number];

const DEFAULT_VOLUME: VolumeBasis = 50_000;

// Normalizes any pricing shape to a total bill at a given page count. This is
// the one function allowed to collapse the union to a number — everywhere else
// keeps the real shape (see costRangeLabel, which reports each tier in its own
// native unit).
//
// Note this takes `pages` rather than closing over a constant: `subscription`
// is the one shape where cost is NOT proportional to volume, and that only
// shows up if volume is an input.
function costAtVolume(tier: CostTier, pages: number): number {
  switch (tier.kind) {
    case "metered":
      return ((tier.low + tier.high) / 2 / 1000) * pages;
    case "flatFee":
      return (tier.amount / tier.forPages) * pages;
    case "subscription":
      // No cap: one flat monthly fee, whatever the volume. Capped: as many
      // months as it takes to clear the pages.
      return tier.pageCapPerMonth === null
        ? tier.monthlyAmount
        : tier.monthlyAmount * Math.ceil(pages / tier.pageCapPerMonth);
    case "perToken":
      return (tier.usdPerMillionTokens / 1_000_000) * tier.tokensPerPage * pages;
    case "computeTime":
      return tier.usdPerComputeSecond * tier.secondsPerPage * pages;
    default:
      return assertNever(tier);
  }
}

function costPerPageAtVolume(tier: CostTier, pages: number): number {
  return costAtVolume(tier, pages) / pages;
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
      return tier.pageCapPerMonth === null
        ? `$${tier.monthlyAmount.toLocaleString()}/mo, no stated cap`
        : `$${tier.monthlyAmount.toFixed(2)}/mo, cap ${tier.pageCapPerMonth.toLocaleString()} pages`;
    case "perToken":
      return `$${tier.usdPerMillionTokens.toFixed(2)}/1M tok × ${tier.tokensPerPage.toLocaleString()} tok/pg`;
    case "computeTime":
      return `$${tier.usdPerComputeSecond.toFixed(4)}/compute-sec × ${tier.secondsPerPage}s/pg`;
    default:
      return assertNever(tier);
  }
}

function costKindLabel(tier: CostTier): string {
  switch (tier.kind) {
    case "metered": return "Per page";
    case "flatFee": return "Flat fee";
    case "subscription": return "Subscription";
    case "perToken": return "Per token";
    case "computeTime": return "Compute time";
    default: return assertNever(tier);
  }
}

// ============================================================================
// Confidence — the dimension the price alone doesn't show
// ============================================================================

function confidenceLabel(c: SourceConfidence): string {
  switch (c) {
    case "verified": return "Verified";
    case "vendor-email": return "Vendor email";
    case "derived": return "Derived";
    case "disputed": return "Rate disputed";
    case "unverified": return "Unverified";
    default: return assertNever(c);
  }
}

// Longer-form, for the badge tooltip — what the label actually means about
// how much weight the number can carry.
function confidenceMeaning(c: SourceConfidence): string {
  switch (c) {
    case "verified": return "Checked against the vendor's own published rate.";
    case "vendor-email": return "Confirmed directly by the vendor in writing, not from a public page.";
    case "derived": return "Arithmetic from a different billing unit (e.g. GPU-seconds) at an assumed throughput — not a rate the vendor quotes.";
    case "disputed": return "A real published rate exists and materially disagrees with the figure used.";
    case "unverified": return "No vendor source could be found to confirm this figure at all.";
    default: return assertNever(c);
  }
}

// Only the first two are figures you could defend in a budget review.
function isTrustworthy(c: SourceConfidence): boolean {
  switch (c) {
    case "verified":
    case "vendor-email":
      return true;
    case "derived":
    case "disputed":
    case "unverified":
      return false;
    default: return assertNever(c);
  }
}

// A CSS custom property per confidence level, reusing the page's existing
// semantic palette rather than introducing new theme tokens.
function confidenceTone(c: SourceConfidence): string {
  switch (c) {
    case "verified": return "var(--lapis)";
    case "vendor-email": return "var(--lapis-soft)";
    case "derived": return "var(--gold)";
    case "disputed": return "var(--vermilion)";
    case "unverified": return "var(--ink-faint)";
    default: return assertNever(c);
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
// Cost bar chart / dot plot / table — all three driven entirely through
// costAtVolume / costRangeLabel, so they are guaranteed to agree (no separate
// "chart number" vs "table number" that can silently drift apart, which is
// exactly what happened across the plain-JS cost.md / fastocr.md files this
// data was extracted from).
//
// The $-at-volume figures are computed from each row's native rate on every
// render. The source CSV stored "cost at 1k" and "cost at 50k" as two typed-in
// columns and accumulated a 10× error on one row and a 5× error on another —
// both of which are now impossible to reproduce here, because there is only
// one number per row and the rest is arithmetic.
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

// A compact "how much is this number worth" badge, used in every view so the
// price and its credibility are never shown apart.
function confidenceBadge(c: SourceConfidence): string {
  return `<span class="conf-badge" style="--conf: ${confidenceTone(c)}" title="${confidenceLabel(c)} — ${confidenceMeaning(c)}">${confidenceLabel(c)}</span>`;
}

function rowTooltip(row: PricingRow, value: number, pages: VolumeBasis): string {
  return `${row.model} · ${costKindLabel(row.cost)}: ${costRangeLabel(row.cost)} → ${formatUSD(value)} per ${pages.toLocaleString()} pages (${confidenceLabel(row.confidence)})`;
}

// Marks that ride along with a row's name in both charts: the engine was
// actually run here, and/or the review changed its number.
function rowChips(row: PricingRow, isCheapest: boolean): string {
  return (
    (isCheapest ? '<span class="cheapest-chip">cheapest</span>' : "") +
    (row.benchmarked ? '<span class="run-chip" title="This engine was actually run against the source document — see its OCR output below">run</span>' : "") +
    (row.corrected ? '<span class="corrected-chip" title="The review corrected this figure from the original table">corrected</span>' : "")
  );
}

function buildBarChart(rows: PricingRow[], values: number[], cheapestValue: number, pages: VolumeBasis): HTMLElement {
  const max = Math.max(...values);
  const chart = el("div", "chart");
  rows.forEach((row, i) => {
    const value = values[i]!;
    const isCheapest = value === cheapestValue;
    const pct = Math.max(2, (value / max) * 100);
    const barRow = el("div", "bar-row");
    barRow.innerHTML = `
      <div class="name">${row.model}${rowChips(row, isCheapest)}</div>
      <div class="bar-track"><div class="bar-fill${isCheapest ? " bar-cheapest" : ""}" data-pct="${pct}"
           style="background: ${confidenceTone(row.confidence)}"
           title="${rowTooltip(row, value, pages)}"></div></div>
      <div class="value">${formatUSD(value)}</div>
    `;
    chart.appendChild(barRow);
  });
  return chart;
}

function buildDotPlot(rows: PricingRow[], values: number[], cheapestValue: number, pages: VolumeBasis): HTMLElement {
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

  rows.forEach((row, i) => {
    const value = values[i]!;
    const isCheapest = value === cheapestValue;
    const pct = Math.min(100, (value / axisMax) * 100);
    const dotRow = el("div", "dot-row");
    dotRow.innerHTML = `
      <div class="name">${row.model}${rowChips(row, isCheapest)}</div>
      <div class="dot-track">
        <div class="dot-line"></div>
        <div class="dot${isCheapest ? " dot-cheapest" : ""}" data-pct="${pct}"
             style="background: ${confidenceTone(row.confidence)}"
             title="${rowTooltip(row, value, pages)}"></div>
      </div>
      <div class="value">${formatUSD(value)}</div>
    `;
    plot.appendChild(dotRow);
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

function buildCostTable(rows: PricingRow[], values: number[], cheapestValue: number, pages: VolumeBasis): HTMLElement {
  const table = el(
    "table",
    "cost-table",
    `<thead><tr>
       <th>Model</th><th>Provider / hosting</th><th>Billing shape</th>
       <th>$ per ${pages.toLocaleString()} pages</th><th>Effective $/page</th>
       <th>Native rate</th><th>Confidence</th><th>Review note</th>
     </tr></thead>`
  );
  const tbody = el("tbody");
  rows.forEach((row, i) => {
    const value = values[i]!;
    const perPage = costPerPageAtVolume(row.cost, pages);
    tbody.appendChild(
      el(
        "tr",
        row.corrected ? "row-corrected" : undefined,
        `<td>${row.model}${value === cheapestValue ? " 🏆" : ""}${row.benchmarked ? '<span class="run-chip">run</span>' : ""}</td>
         <td>${row.provider}</td>
         <td>${costKindLabel(row.cost)}</td>
         <td class="num">${formatUSD(value)}</td>
         <td class="num">$${perPage.toFixed(5)}</td>
         <td>${costRangeLabel(row.cost)}<br><span class="ceiling">ceiling: ${row.ceiling} · ${row.worstCase}</span></td>
         <td>${confidenceBadge(row.confidence)}</td>
         <td class="note">${row.note}</td>`
      )
    );
  });
  table.appendChild(tbody);
  const wrap = el("div", "table-wrap");
  wrap.appendChild(table);
  return wrap;
}

function buildConfidenceLegend(): HTMLElement {
  const order: SourceConfidence[] = ["verified", "vendor-email", "derived", "disputed", "unverified"];
  const legend = el("div", "conf-legend");
  legend.innerHTML =
    `<span class="conf-legend-label">Bar colour = confidence in the figure</span>` +
    order
      .map(
        (c) =>
          `<span class="conf-legend-item" title="${confidenceMeaning(c)}"><i style="background:${confidenceTone(c)}"></i>${confidenceLabel(c)}</span>`
      )
      .join("");
  return legend;
}

(function initCost(): void {
  // Section state. Volume is a real axis here, not a constant: it is the only
  // way the flat-subscription row's behaviour becomes visible.
  let volume: VolumeBasis = DEFAULT_VOLUME;
  let trustedOnly = false;
  let currentView = "bar";
  let revealed = false;

  const barView = must<HTMLElement>("barView");
  const dotView = must<HTMLElement>("dotView");
  const tableView = must<HTMLElement>("tableView");
  const statsHost = must<HTMLElement>("costStats");
  const anomalyHost = must<HTMLElement>("anomalyCallouts");

  function visibleRows(): PricingRow[] {
    const rows = trustedOnly ? PRICING_ROWS.filter((r) => isTrustworthy(r.confidence)) : [...PRICING_ROWS];
    return rows.sort((a, b) => costAtVolume(a.cost, volume) - costAtVolume(b.cost, volume));
  }

  // Headline numbers, computed rather than written down, so they can't drift
  // out of sync with the table the way the source CSV's own totals did.
  function renderStats(): void {
    const trusted = PRICING_ROWS.filter((r) => isTrustworthy(r.confidence));
    const corrected = PRICING_ROWS.filter((r) => r.corrected);
    const all = [...PRICING_ROWS].sort((a, b) => costAtVolume(a.cost, volume) - costAtVolume(b.cost, volume));
    const cheapest = all[0]!;
    const dearest = all[all.length - 1]!;
    const spread = costAtVolume(dearest.cost, volume) / costAtVolume(cheapest.cost, volume);

    statsHost.innerHTML = [
      { n: `${trusted.length} of ${PRICING_ROWS.length}`, l: "figures vendor-confirmed" },
      { n: `${corrected.length}`, l: "corrected during review" },
      { n: `${spread < 10 ? spread.toFixed(1) : Math.round(spread).toLocaleString()}×`, l: `spread, cheapest → dearest at ${volume.toLocaleString()} pages` },
      { n: formatUSD(costAtVolume(cheapest.cost, volume)), l: `cheapest (${cheapest.model})` },
    ]
      .map((s) => `<div class="cost-stat"><b>${s.n}</b><span>${s.l}</span></div>`)
      .join("");
  }

  function renderAnomalies(): void {
    anomalyHost.innerHTML = "";

    // Rows whose bill does not scale with volume at all — the reason this
    // section has a volume toggle in the first place.
    PRICING_ROWS.filter((r) => r.cost.kind === "subscription").forEach((r) => {
      const at1k = costPerPageAtVolume(r.cost, 1_000);
      const at50k = costPerPageAtVolume(r.cost, 50_000);
      anomalyHost.appendChild(
        el(
          "div",
          "anomaly-callout",
          `<b>${r.model} is the only row that doesn't scale.</b> It bills a flat ${costRangeLabel(r.cost)}, so the invoice is identical at 1,000 and 50,000 pages — the same fee spread over 50× the work. Effective cost falls from <b>$${at1k.toFixed(5)}/page</b> to <b>$${at50k.toFixed(5)}/page</b> across that range. Every other row is linear, so at low volume this is the most expensive option on the table by a wide margin, and at high volume it stops being an outlier. Flip the volume toggle above to watch it move.`
        )
      );
    });

    // Real invoices for this specific document, where the quoted tier and what
    // was actually charged disagree. Generated from data rather than
    // hand-written, so a 7th engine with the same story needs no new prose.
    DATA.models
      .filter((m): m is ModelResult & { actualRunCost: CostTier } => m.actualRunCost !== undefined)
      .forEach((m) => {
        const row = primaryPricingRow(m.id);
        const listed = row
          ? `the ${costKindLabel(row.cost).toLowerCase()} rate listed above (${costRangeLabel(row.cost)} ≈ ${formatUSD(costAtVolume(row.cost, volume))} per ${volume.toLocaleString()} pages)`
          : "the rate listed above";
        anomalyHost.appendChild(
          el(
            "div",
            "anomaly-callout",
            `<b>What ${m.name} actually charged for this document: ${costRangeLabel(m.actualRunCost)}.</b> That is a real invoice for the exact 5-page file in this demo, not a rate card — which is why it isn't what the chart plots. The chart uses ${listed} so all twelve rows compare on one basis. ${m.note}`
          )
        );
      });
  }

  function renderCharts(): void {
    const rows = visibleRows();
    const values = rows.map((r) => costAtVolume(r.cost, volume));
    const cheapestValue = Math.min(...values);

    const captionText = `Sorted cheapest → most expensive at <b>${volume.toLocaleString()} pages</b>, normalized from each vendor's native billing unit (per page / per token / per compute-second / flat subscription) — hover a mark for the real shape behind the number. Colour encodes how much the figure can be trusted, not how big it is. Figures transcribed from <code>OCR pricing - final.csv</code>; to change them, edit <code>PRICING_ROWS</code> in <code>demo/typescript-src/data.ts</code> and rebuild with <code>python3 demo/build.py</code>.`;

    barView.innerHTML = "";
    const barChart = buildBarChart(rows, values, cheapestValue, volume);
    barView.appendChild(buildConfidenceLegend());
    barView.appendChild(barChart);
    barView.appendChild(el("div", "chart-caption", captionText));

    dotView.innerHTML = "";
    const dotPlot = buildDotPlot(rows, values, cheapestValue, volume);
    dotView.appendChild(buildConfidenceLegend());
    dotView.appendChild(dotPlot);
    dotView.appendChild(el("div", "chart-caption", captionText));

    tableView.innerHTML = "";
    tableView.appendChild(buildCostTable(rows, values, cheapestValue, volume));

    if (revealed) reveal();
  }

  // Fills start at width 0 / left 0 and are released once the section is on
  // screen. After the first reveal, re-renders animate from 0 again on the
  // next frame, which reads as the bars re-settling to the new volume.
  function reveal(): void {
    revealed = true;
    requestAnimationFrame(() => {
      barView.querySelectorAll<HTMLElement>(".bar-fill").forEach((f) => { f.style.width = f.dataset.pct + "%"; });
      dotView.querySelectorAll<HTMLElement>(".dot").forEach((d) => { d.style.left = d.dataset.pct + "%"; });
    });
  }

  function renderAll(): void {
    renderStats();
    renderCharts();
    renderAnomalies();
  }

  renderAll();

  const io = new IntersectionObserver((entries) => {
    entries.forEach((e) => {
      if (e.isIntersecting) {
        reveal();
        io.unobserve(e.target);
      }
    });
  }, { threshold: 0.15 });
  io.observe(must<HTMLElement>("cost"));

  const views: Record<string, HTMLElement> = { bar: barView, dot: dotView, table: tableView };
  function applyView(): void {
    for (const [key, view] of Object.entries(views)) {
      view.style.display = key === currentView ? "" : "none";
    }
  }
  applyView();

  must<HTMLElement>("viewToggle").addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!btn) return;
    currentView = btn.dataset.view ?? "bar";
    document.querySelectorAll<HTMLButtonElement>("#viewToggle button").forEach((b) => b.classList.toggle("active", b === btn));
    reveal(); // in case the section never scrolled past the observer threshold
    applyView();
  });

  must<HTMLElement>("volumeToggle").addEventListener("click", (e) => {
    const btn = (e.target as HTMLElement).closest<HTMLButtonElement>("button");
    if (!btn) return;
    const picked = Number(btn.dataset.volume);
    const match = VOLUME_BASES.find((v) => v === picked);
    if (match === undefined) return;
    volume = match;
    document.querySelectorAll<HTMLButtonElement>("#volumeToggle button").forEach((b) => b.classList.toggle("active", b === btn));
    renderAll();
    applyView();
  });

  const trustBtn = must<HTMLButtonElement>("trustFilter");
  trustBtn.addEventListener("click", () => {
    trustedOnly = !trustedOnly;
    trustBtn.classList.toggle("active", trustedOnly);
    trustBtn.setAttribute("aria-pressed", String(trustedOnly));
    trustBtn.textContent = trustedOnly ? "Showing vendor-confirmed only" : "Show vendor-confirmed only";
    renderAll();
    applyView();
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
      const row = primaryPricingRow(m.id);

      let flagBanner = "";
      if (m.flags.includes("low-quality")) {
        flagBanner = `<div class="flag-banner">⚠ <span><b>Flagged: low-quality output.</b> This run's text doesn't reliably track the source document — it reads as a garbled or failed OCR pass. Shown as-is rather than hidden, since an honest benchmark includes the misses.</span></div>`;
      }
      // Every panel states what the engine's price is worth, not just what it
      // is — the same confidence the chart colours by.
      if (row) {
        flagBanner += `<div class="flag-banner">🏷 <span><b>Listed at ${costRangeLabel(row.cost)}</b> (${costKindLabel(row.cost).toLowerCase()}, ${row.provider}) — ${confidenceBadge(row.confidence)} ${confidenceMeaning(row.confidence)}${row.corrected ? " This figure was corrected during review." : ""}</span></div>`;
      }
      if (m.actualRunCost) {
        flagBanner += `<div class="flag-banner">💰 <span><b>Cost anomaly on this run.</b> Running this exact 5-page document cost <b>${costRangeLabel(m.actualRunCost)}</b> (${costKindLabel(m.actualRunCost)}) — the chart above uses the listed rate instead so all twelve priced rows compare on the same basis.</span></div>`;
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
        <p class="panel-note">${m.note}</p>
        <div class="ocr-text">${renderText(m.text)}</div>
      `
      );
    }
  );
})();

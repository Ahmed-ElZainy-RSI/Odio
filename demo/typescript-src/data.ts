// ============================================================================
// Model manifest — EDIT THIS FILE to add engines or change prices.
//
// This is the file you actually touch. It has no `text` field on purpose:
// the OCR output shown in each tab is read straight from `file` (a filename
// inside models/) and cleaned by the build script, so "modifying results"
// means editing or replacing that .md file directly — not this one.
//
// After editing, rebuild with:
//   python3 demo/build.py
//
// tsc type-checks this file against ModelMeta/CostTier/ModelId (defined in
// app.ts) before anything is built — a missing field, a misspelled CostTier
// `kind`, or a ModelId not yet added to app.ts's union all fail the build
// with a specific error, rather than shipping a broken card silently.
// ============================================================================

const MODEL_META: ModelMeta[] = [
  {
    id: "lighton",
    name: "LightOn",
    engine: "lighting-2-ocr-1b (LightOnOCR-2-1B)",
    file: "lighton-output-Ibn_Battuta_in_Egypt.pdf.md",
    cost: { kind: "metered", low: 4.30, high: 4.30 },
    costQuote: "LightOn Console: flat metered rate ≈$4.30 per 1,000 pages (~$0.0043/page). Pay-as-you-go minimum top-up is €5 (~3,000 pages).",
    flags: [],
  },
  {
    id: "datalab",
    name: "Datalab",
    engine: "Surya OCR 2",
    file: "datalab-output-Ibn_Battuta_in_Egypt.pdf.md",
    cost: { kind: "metered", low: 2.00, high: 4.00 },
    costQuote: "Replicate Managed API: $2.00–$4.00 per 1,000 pages ($0.002–$0.004/page). Note: a separate research pass quoted $300–$1,250 per 50,000 pages for this same tier, which computes to $6–$25/1,000 pages — inconsistent with the figure above by 3–6×. Shown here rather than silently resolved; the $2–$4/1k figure is used for the chart.",
    flags: [],
  },
  {
    id: "paddleocr",
    name: "PaddleOCR VL 1.6",
    engine: "PP-OCR-VL 1.6",
    file: "Ibn_Battuta_in_Egypt.pdf_by_PaddleOCR-VL-1.6.md",
    cost: { kind: "metered", low: 1.50, high: 5.00 },
    costQuote: "AWS Marketplace: $2.00 per 1,000 pages ($0.002/page). WaveSpeedAI: $5.00 per 1,000 pages ($0.005/page).",
    flags: [],
  },
  {
    id: "fastocr",
    name: "FastOCR",
    engine: "Business API tier",
    file: "Ibn_Battuta_in_Egypt-ocr-fastocr.md",
    cost: { kind: "metered", low: 1.50, high: 2.00 },
    costQuote: "Business API Tier (quoted): $1.50–$2.00 per 1,000 pages (est., requires an emailed sales quote). Business/Enterprise tiers appear to enforce a base account minimum regardless of document size — see the run anomaly below.",
    actualRunCost: { kind: "flatFee", amount: 249.95, forPages: 5 },
    flags: ["anomaly"],
  },
  {
    id: "baseerocr",
    name: "Besser OCR",
    engine: "Enterprise tier",
    file: "Ibn_Battuta_in_Egypt_baseerocr.md",
    cost: { kind: "subscription", monthlyAmount: 66.0, pageCapPerMonth: 2000 },
    costQuote: "249 SAR/month (≈$66/month) subscription capped at 2,000 pages/month — $33.00 per 1,000 pages at full capacity (66/2000), but the fee doesn't scale down: a one-off small job still bills the full month.",
    actualRunCost: { kind: "flatFee", amount: 66.0, forPages: 5 },
    flags: ["anomaly"],
  },
  {
    id: "glmocr",
    name: "GLM-OCR",
    engine: "GLM-4V (Z.ai API)",
    file: "glm_ocr_Ibn_Battuta_in_Egypt-2026-07-21_05-35-48.md",
    cost: { kind: "metered", low: 0.20, high: 0.70 },
    costQuote: "Z.ai Developer API: $0.20–$0.70 per 1,000 pages ($0.0002–$0.0007/page) — cheapest of the six, but requires converting PDFs to image arrays client-side before submission.",
    flags: ["low-quality"],
  },

  // --------------------------------------------------------------------
  // TEMPLATE — copy this block to add a new engine. Steps:
  //   1. Drop the OCR output file in models/ (any filename).
  //   2. Add its id to the ModelId union near the top of app.ts.
  //   3. Uncomment and fill in a copy of this block.
  //   4. Run `python3 demo/build.py`.
  //
  // {
  //   id: "yourEngineId",                 // must match a ModelId in app.ts
  //   name: "Display Name",
  //   engine: "model / version label",
  //   file: "your-output-file.md",        // must exist in models/
  //
  //   // Pick exactly one CostTier shape:
  //   cost: { kind: "metered", low: 0, high: 0 },                       // $/1,000 pages, a range or flat (low === high)
  //   // cost: { kind: "flatFee", amount: 0, forPages: 0 },             // a fixed price for a fixed page count
  //   // cost: { kind: "subscription", monthlyAmount: 0, pageCapPerMonth: 0 }, // monthly plan with a page cap
  //
  //   costQuote: "One-line source note for the pricing above.",
  //   actualRunCost: undefined,           // optional: a real one-off CostTier for this exact document
  //   flags: [],                          // "low-quality" and/or "anomaly", or leave empty
  // },
  // --------------------------------------------------------------------
];

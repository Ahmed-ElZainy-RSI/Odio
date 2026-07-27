// ============================================================================
// Model manifest + audited price list — EDIT THIS FILE to add engines or
// change prices.
//
// Two separate datasets live here, on purpose:
//
//   MODEL_META   the engines actually RUN against the source document. Drives
//                the OCR-output tabs. Has no `text` field: the output shown in
//                each tab is read straight from `file` (a filename inside
//                models/) and cleaned by the build script, so "modifying
//                results" means editing that .md file, not this one.
//
//   PRICING_ROWS the audited price list, transcribed from
//                `OCR pricing - final.csv`. Drives the cost chart/table. It is
//                DELIBERATELY not 1:1 with MODEL_META: it prices 12 rows,
//                including four engines never benchmarked here, and three
//                separate pricing scenarios for the single PaddleOCR run.
//                Collapsing the two would mean either dropping real priced
//                options or inventing OCR output that was never produced.
//
// Every figure carries a `confidence` and the reviewer's `note` verbatim.
// That is the point of this table: only two of the twelve rows are
// vendor-confirmed, and the cheapest seven are all unverified or disputed.
//
// After editing, rebuild with:
//   python3 demo/build.py
//
// tsc type-checks this file against ModelMeta/PricingRow/CostTier/ModelId/
// SourceConfidence (defined in app.ts) before anything is built — a missing
// field, a misspelled CostTier `kind`, or a ModelId not yet added to app.ts's
// union all fail the build with a specific error, rather than shipping a
// broken card silently.
// ============================================================================

const MODEL_META: ModelMeta[] = [
  {
    id: "lighton",
    name: "LightOn",
    engine: "lighting-2-ocr-1b (LightOnOCR-2-1B)",
    file: "lighton-output-Ibn_Battuta_in_Egypt.pdf.md",
    note: "Pay-as-you-go minimum top-up is €5 (~3,000 pages), so the smallest real spend is well above a single-document job.",
    flags: [],
  },
  {
    id: "datalab",
    name: "Datalab",
    engine: "Surya OCR 2",
    file: "datalab-output-Ibn_Battuta_in_Egypt.pdf.md",
    note: "Priced here as the Surya 2 (Balanced) row. Replicate bills dedicated deployments per GPU-second, not per page — the per-page figure is derived at an unstated throughput assumption, which is why it carries `derived` confidence rather than a vendor rate.",
    flags: [],
  },
  {
    id: "paddleocr",
    name: "PaddleOCR VL 1.6",
    engine: "PP-OCR-VL 1.6",
    file: "Ibn_Battuta_in_Egypt.pdf_by_PaddleOCR-VL-1.6.md",
    note: "The one engine with three separate priced scenarios in the table below — token-standard, token-dense, and a flat per-page wrapper — spanning a 4× spread for the same model.",
    flags: [],
  },
  {
    id: "fastocr",
    name: "FastOCR",
    engine: "Business API tier",
    file: "Ibn_Battuta_in_Egypt-ocr-fastocr.md",
    // A real invoice for this exact 5-page run — not a rate card, and not in
    // the CSV. Kept because it contradicts the quoted tier by ~1,000×.
    actualRunCost: { kind: "flatFee", amount: 249.95, forPages: 5 },
    note: "Business/Enterprise tiers appear to enforce a base account minimum regardless of document size — see the run anomaly below.",
    flags: ["anomaly"],
  },
  {
    id: "baseerocr",
    name: "Besser OCR",
    engine: "Enterprise tier",
    file: "Ibn_Battuta_in_Egypt_baseerocr.md",
    actualRunCost: { kind: "flatFee", amount: 66.0, forPages: 5 },
    note: "Unresolved tier discrepancy: this run was billed on a 249 SAR/month (~$66) plan capped at 2,000 pages/month, while the vendor-confirmed row in the table below prices the flat SAR 5,000 Enterprise subscription (~$1,333/month, no stated cap). Both are shown rather than silently reconciled — confirm which tier applies before quoting either.",
    flags: ["anomaly"],
  },
  {
    id: "glmocr",
    name: "GLM-OCR",
    engine: "GLM-4V (Z.ai API)",
    file: "glm_ocr_Ibn_Battuta_in_Egypt-2026-07-21_05-35-48.md",
    note: "Cheapest row in the table, but requires converting PDFs to image arrays client-side before submission — and the rate itself could not be confirmed.",
    flags: ["low-quality"],
  },

  // --------------------------------------------------------------------
  // TEMPLATE — copy this block to add a new engine. Steps:
  //   1. Drop the OCR output file in models/ (any filename).
  //   2. Add its id to the ModelId union near the top of app.ts.
  //   3. Uncomment and fill in a copy of this block.
  //   4. Add a matching PRICING_ROWS entry with `benchmarked: "yourEngineId"`.
  //   5. Run `python3 demo/build.py`.
  //
  // {
  //   id: "yourEngineId",                 // must match a ModelId in app.ts
  //   name: "Display Name",
  //   engine: "model / version label",
  //   file: "your-output-file.md",        // must exist in models/
  //   note: "Operational note for this engine (not pricing).",
  //   actualRunCost: undefined,           // optional: real invoice for THIS document
  //   flags: [],                          // "low-quality" and/or "anomaly", or leave empty
  // },
  // --------------------------------------------------------------------
];

// ============================================================================
// Audited price list — transcribed from `OCR pricing - final.csv`.
//
// Rates are stored in each vendor's NATIVE unit (per-million-tokens, per-page,
// per-compute-second, per-month) and the $-at-volume figures are computed from
// them by costAtVolume() in app.ts. Nothing here stores "cost at 1k" and "cost
// at 50k" as two hand-typed numbers — that is precisely how the source table
// accumulated the 10× and 5× arithmetic errors called out in the notes below.
//
// `note` is the reviewer's verbatim finding. `confidence` is what the figure is
// actually worth. `benchmarked` links a row to an OCR tab when that engine was
// genuinely run; null means the price is listed but no output was produced.
// ============================================================================

const PRICING_ROWS: PricingRow[] = [
  {
    model: "GLM-OCR",
    provider: "Z.ai / Alternative Premium Hosts",
    cost: { kind: "perToken", usdPerMillionTokens: 0.2, tokensPerPage: 1_500 },
    ceiling: "$0.20 USD / 1M tokens",
    worstCase: "~1,500 tokens/page",
    confidence: "unverified",
    corrected: true,
    note: "CORRECTED: was $3.00/$150 (10x arithmetic error). Z.ai per-token OCR rate could not be confirmed.",
    benchmarked: "glmocr",
  },
  {
    model: "PaddleOCR-VL-1.6 (Token Standard)",
    provider: "Novita AI / Fireworks AI (Surge Rate)",
    cost: { kind: "perToken", usdPerMillionTokens: 0.1, tokensPerPage: 5_000 },
    ceiling: "$0.10 USD / 1M tokens",
    worstCase: "~5,000 tokens/page",
    confidence: "disputed",
    corrected: false,
    note: "Math OK. Both Novita and Fireworks list this model as 'Serverless: Not supported' (on-demand GPU only) - no per-token serverless rate is published. Aggregators quote Novita ~$0.02/1M, 5x below the ceiling used.",
    benchmarked: "paddleocr",
  },
  {
    model: "DeepSeek OCR 2",
    provider: "SiliconFlow / Official API (Peak Pricing)",
    cost: { kind: "perToken", usdPerMillionTokens: 0.5, tokensPerPage: 1_120 },
    ceiling: "$0.50 USD / 1M tokens",
    worstCase: "~1,120 tokens/page",
    confidence: "disputed",
    corrected: true,
    note: "CORRECTED: was $2.80/$140 (5x arithmetic error). DeepSeek-OCR is free on SiliconFlow and ~$0.03/1M on DeepInfra; $0.50/1M is ~16x published. No public model named 'DeepSeek OCR 2' found.",
    benchmarked: null,
  },
  {
    model: "Unlimited OCR (Serverless)",
    provider: "Managed Third-Party (Unoptimized A100 node)",
    cost: { kind: "computeTime", usdPerComputeSecond: 0.001, secondsPerPage: 1 },
    ceiling: "$0.0010 USD / compute-sec",
    worstCase: "N/A (Heavy document lag)",
    confidence: "unverified",
    corrected: false,
    note: "Math OK but assumes exactly 1 compute-second per page - an assumption, not a rate. No identifiable vendor.",
    benchmarked: null,
  },
  {
    model: "PaddleOCR-VL-1.6 (Token Dense)",
    provider: "Novita AI / Fireworks AI (Surge Rate)",
    cost: { kind: "perToken", usdPerMillionTokens: 0.1, tokensPerPage: 18_000 },
    ceiling: "$0.10 USD / 1M tokens",
    worstCase: "~18,000 tokens/page",
    confidence: "disputed",
    corrected: false,
    note: "Math OK. Same serverless caveat. Note 18,000 tokens/page exceeds Novita's 16,384 context window for this model - not achievable on that host.",
    benchmarked: "paddleocr",
  },
  {
    model: "PaddleOCR-VL-1.6 (Flat Page)",
    provider: "Premium Boutique API Wrappers",
    cost: { kind: "metered", low: 2.0, high: 2.0 },
    ceiling: "$0.0020 USD / page",
    worstCase: "N/A (Fixed cap)",
    confidence: "unverified",
    corrected: false,
    note: "Math OK. No named vendor, so the rate cannot be checked.",
    benchmarked: "paddleocr",
  },
  {
    model: "FastOCR",
    provider: "Developer API (Asynchronous Flow)",
    cost: { kind: "metered", low: 2.0, high: 2.0 },
    ceiling: "Custom Business Tier Quote",
    worstCase: "N/A (Loop script layout)",
    confidence: "unverified",
    corrected: false,
    note: "The $2.00 is an assumption, not a quote - and a custom business tier is exactly what does not scale linearly at 50x. Public FastOCR is a consumer tool at $3.99-$10.99/mo with page allowances.",
    benchmarked: "fastocr",
  },
  {
    model: "LightOnOCR-2-1B",
    provider: "LightOn Platform (FX Rate Fluctuations)",
    // EUR 0.0020/page at EUR/USD 1.1397 → $0.00227940/page → $2.2794 per 1,000.
    cost: { kind: "metered", low: 2.2794, high: 2.2794 },
    ceiling: "EUR 0.0020 (~$0.00228 USD) / page",
    worstCase: "N/A (Fixed rate)",
    confidence: "verified",
    corrected: true,
    note: "CORRECTED FX: was ~$0.00250 (implied EUR/USD 1.25). Actual rate 1.1397 on 2026-07-27. Use 1.25 only as a deliberate FX stress ceiling, labelled as such.",
    benchmarked: "lighton",
  },
  {
    model: "MinerU Extractor",
    provider: "OpenDataLab (Paid SLA / Over-quota Priority)",
    cost: { kind: "metered", low: 5.0, high: 5.0 },
    ceiling: "$0.0050 USD / page",
    worstCase: "N/A (Fixed priority)",
    confidence: "unverified",
    corrected: false,
    note: "Math OK. MinerU documents a 2,000-page/day priority quota with degraded priority beyond it, but no published paid per-page rate was found.",
    benchmarked: null,
  },
  {
    model: "Surya 2 OCR (Balanced)",
    provider: "Dedicated Replicate Deployment (Low Volume)",
    cost: { kind: "metered", low: 10.0, high: 10.0 },
    ceiling: "$0.0100 USD / page",
    worstCase: "N/A (Fixed cap)",
    confidence: "derived",
    corrected: false,
    note: "Replicate dedicated deployments bill per GPU-second, not per page. This is an effective rate at an unstated throughput assumption.",
    benchmarked: "datalab",
  },
  {
    model: "Chandra 2 OCR (Structured)",
    provider: "Dedicated Replicate Deployment (Low Volume)",
    cost: { kind: "metered", low: 15.0, high: 15.0 },
    ceiling: "$0.0150 USD / page",
    worstCase: "N/A (Fixed cap)",
    confidence: "derived",
    corrected: false,
    note: "Same GPU-second caveat. Datalab's own pricing page did not render for confirmation.",
    benchmarked: null,
  },
  {
    model: "Besser OCR",
    provider: "Web / API Portal Ingestion Dashboard",
    // The only non-linear row: a flat monthly fee with no stated page cap, so
    // the bill is identical at 1,000 and 50,000 pages and the effective
    // $/page falls 50× across that range.
    cost: { kind: "subscription", monthlyAmount: 1333.0, pageCapPerMonth: null },
    ceiling: "Flat SAR 5,000 Enterprise Sub",
    worstCase: "N/A (Month-over-month target)",
    confidence: "vendor-email",
    corrected: false,
    note: "Confirmed by user via direct vendor correspondence. Flat monthly fee - does not scale with volume. SAR 5,000 at the 3.75 riyal peg = $1,333.33; table uses $1,333.00. Confirm whether Besser quoted USD directly.",
    benchmarked: "baseerocr",
  },
];

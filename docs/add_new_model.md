# OCR benchmark demo

Two self-contained HTML demos comparing OCR engines on `assets/Ibn_Battuta_in_Egypt.pdf`:

- `ocr_benchmark.html` — plain HTML/CSS/JS, hand-edited directly, no build step.
- `ocr_benchmark_typescript.html` — generated from `typescript-src/` by `build.py`. **This is the one with the extensible, type-checked model manifest — the rest of this doc is about that one.**

Both are single files: fonts, page images, and OCR text are all inlined, so you can double-click either one and it opens correctly in a browser. Never hand-edit `ocr_benchmark_typescript.html` directly — it's regenerated from `typescript-src/` every time you run the build, and any manual changes will be silently overwritten.

## How the pieces fit together

```
demo/
  build.py                      ← run this to rebuild ocr_benchmark_typescript.html
  ocr_benchmark_typescript.html ← generated output, don't hand-edit
  typescript-src/
    data.ts                     ← the model manifest — THIS is what you edit
    app.ts                      ← render engine + shared types (rarely needs touching)
    shell.html                  ← page HTML/CSS
    fonts/                      ← embedded fonts (Amiri, Fraunces), so builds work offline
    package.json                ← npm devDependency on `typescript`
```

`build.py` does five things, in order:

1. Type-checks and compiles `data.ts` + `app.ts` with `tsc --strict`.
2. Extracts the compiled model manifest (`MODEL_META`) back out as JSON by running it in Node.
3. For each manifest entry, reads `models/<file>`, strips its `Cost:` block and any image references, and uses that as the model's displayed OCR text.
4. Renders `assets/Ibn_Battuta_in_Egypt.pdf` to page images (thumbnail + full-size).
5. Assembles `typescript-src/shell.html` with the fonts, compiled JS, and model/page data into the final standalone `ocr_benchmark_typescript.html`.

## Requirements

- **Node.js + npm** on `PATH` (used to type-check and compile `data.ts`/`app.ts`). One-time setup:
  ```
  cd demo/typescript-src && npm install
  ```
- **Python packages:** `pymupdf`, `pillow`
  ```
  pip install pymupdf pillow
  ```

## Quick start

```
python3 demo/build.py
```

If it fails, read the error — `tsc` errors point at the exact line in `data.ts` that's wrong (see "Why this is safe" below).

---

## Adding a new model

Walking through it with a 7th, hypothetical engine called `"newengine"`:

**1. Get its OCR output into `models/`.** Any filename works, as long as it's readable text (markdown or plain text):
```
models/newengine-output-Ibn_Battuta_in_Egypt.pdf.md
```
If the file has a `Cost:` block or embedded images (like the existing `fastocr`/`paddleocr` files do), the build script strips those out automatically — you don't need to clean the file yourself.

**2. Add its id to the `ModelId` union**, near the top of `app.ts`:
```ts
type ModelId = "lighton" | "datalab" | "paddleocr" | "fastocr" | "baseerocr" | "glmocr" | "newengine";
```
This is a deliberate two-step process (id here, entry in `data.ts`) rather than accepting any string — see "Why this is safe" below for what it buys you.

**3. Add an entry to `MODEL_META` in `data.ts`.** There's a commented-out template block at the bottom of the file ready to copy; using the real `lighton` entry as a concrete reference:
```ts
{
  id: "lighton",
  name: "LightOn",
  engine: "lighting-2-ocr-1b (LightOnOCR-2-1B)",
  file: "lighton-output-Ibn_Battuta_in_Egypt.pdf.md",
  cost: { kind: "metered", low: 4.30, high: 4.30 },
  costQuote: "LightOn Console: flat metered rate ≈$4.30 per 1,000 pages (~$0.0043/page). Pay-as-you-go minimum top-up is €5 (~3,000 pages).",
  flags: [],
},
```
Note there's **no `text` field** — that's intentional. The displayed OCR output always comes from step 1's file, read fresh on every build, so you never hand-copy a big text blob into TypeScript source.

**4. Rebuild:**
```
python3 demo/build.py
```
The new model shows up as a 6th (now 7th) tab, and its price shows up in the cost bar chart / dot plot / table automatically — nothing else needs to change.

---

## Changing prices

Edit the `cost` field for that model in `data.ts`, then rebuild. `cost` must be exactly one of three shapes (a TypeScript discriminated union — pick the `kind` that matches how the vendor actually prices it, don't force a metered number where it isn't one):

| `kind` | Fields | Use for | Example (from `data.ts`) |
|---|---|---|---|
| `"metered"` | `low`, `high` (both **$ per 1,000 pages**) | A per-page rate, possibly quoted as a range. Use `low === high` for a flat rate. | `{ kind: "metered", low: 4.30, high: 4.30 }` — LightOn |
| `"flatFee"` | `amount` (**$**), `forPages` (page count) | A fixed price for a fixed batch, unrelated to a per-page rate. | `{ kind: "flatFee", amount: 249.95, forPages: 5 }` — FastOCR's actual 5-page run |
| `"subscription"` | `monthlyAmount` (**$/month**), `pageCapPerMonth` | A monthly plan with a page ceiling. | `{ kind: "subscription", monthlyAmount: 66.0, pageCapPerMonth: 2000 }` — Besser OCR |

Whatever shape you pick, every chart/table on the page normalizes it to **$ per 50,000 pages** the same way (`costToChartBasis` in `app.ts`), so a `metered` model and a `subscription` model still compare on equal footing without you doing any conversion math yourself.

`costQuote` is a free-text one-liner shown as the source note in the table view and in tooltips — put whatever citation/detail justifies the number there.

### Documenting a real one-off cost that differs from the quoted rate

If you actually ran a document through a paid tier and it cost something different from the metered/subscription estimate (like FastOCR's real $249.95-for-5-pages, or Besser's subscription fee not scaling down for a small job), add an `actualRunCost` — same `CostTier` shape, any of the three `kind`s:
```ts
actualRunCost: { kind: "flatFee", amount: 66.0, forPages: 5 },
```
and add `"anomaly"` to that model's `flags`. This automatically:
- puts a ⚠ next to the model's name in the bar/dot chart,
- generates a "Why isn't X's real cost in the chart above?" callout under the cost section (this is written once, generically, in `app.ts` — it isn't hand-written per model, so a 3rd/4th model with the same story doesn't need any new code),
- shows the detail in that model's results tab.

---

## Modifying results (the OCR text shown per model)

Edit or replace the file in `models/` that the manifest entry's `file` field points at, then rebuild. Don't edit the OCR text in `data.ts` — there isn't any to edit there; it's read from `models/` fresh every build. If you want to point a model at a different/newer output file, just change its `file` field.

## Other flags

- `"low-quality"` — shown as a ⚠ badge on that model's tab and in the table. Used on `glmocr` currently, since its output doesn't reliably track the source document. Use this for a garbled/failed run you want to show honestly rather than hide.

---

## Why this is safe (and what breaks the build on purpose)

`data.ts` is real, compiled TypeScript, type-checked against `app.ts`'s `ModelMeta`/`CostTier`/`ModelId` definitions before anything is built. Concretely, these all fail the build with a specific file/line, rather than shipping something broken:

- A model id used in `data.ts` that wasn't added to `ModelId` in `app.ts` (step 2 above).
- A typo'd `CostTier` field name (e.g. `lowPrice` instead of `low`).
- A missing required field on a manifest entry (e.g. forgetting `costQuote`).

If you see a `tsc` error when running `python3 demo/build.py`, that's the compiler telling you exactly what's wrong in `data.ts` — fix that line and rerun.

## Troubleshooting

- **`No tsc found`** — run `npm install` inside `demo/typescript-src/` once (needs Node/npm on `PATH`).
- **`models/<file> ... does not exist`** — the `file` field in a `data.ts` entry doesn't match an actual filename in `models/`; check for typos.
- **`ModuleNotFoundError: No module named 'fitz'` (or `PIL`)** — `pip install pymupdf pillow`.

# OCR Post-Correction — Two Proposed Approaches

> Both approaches share the same principle: **detection is cheap and runs on everything; verification is expensive and runs only on what's suspicious.** The difference is who makes the final call on the correct word — a single visual model, or a vote among multiple OCR engines.

| | Approach 2 | Approach 3 |
|---|---|---|
| **Name** | Context Model + Visual Verification | OCR Ensemble |
| **Decider** | A VLM reading the crop | Majority vote across 3 engines |
| **Section** | [Sections 1–8](#1-core-idea) | [Section 9](#9-approach-3--ocr-ensemble) |

---

# Approach 2 — Context Model + Visual Verification

> A context model finds **where the problem is**; a visual model determines **what is actually written** in the original image.

---

## 1. Core Idea

The problem with correcting OCR using an LLM alone is that it guesses from context, so it can invent a word that is linguistically plausible but not the one on the page. The problem with running Vision on every page is cost and latency.

The solution: separate two very different jobs.

| Model | Question it answers | Output |
|---|---|---|
| **Context Model** | Where is the problem? | List of suspicious words + suspicion score |
| **Visual Verifier** | What is actually written? | Correct word from the image + confidence |

The Context Model is **not allowed to correct** — its role is **detection only**. The final decision always traces back to the image.

---

## 2. Architecture

```
LightOn
   ↓
OCR Text
   ↓
Context Model
   ↓
العنصر ⚠️
   ↓
Get Bounding Box
   ↓
Crop from image
   ↓
Visual OCR / VLM
   ↓
العنبر ✅
```

### At full-batch scale

```
50,000 pages
     ↓
LightOn                      ← one pass over everything
     ↓
Context Review               ← cheap, text only
     ↓
Suspicious words only        ← typically 1–4% of tokens
     ↓
Visual Verification          ← expensive, but on a small slice
```

**Key point:** you do not run Vision on all 50,000 pages. You run it only on the suspicious words.

---

## 3. Worked Example

**OCR output:**

```
حوت العنصر يعيش في المحيط.
("The [element] whale lives in the ocean.")
```

**Context Model assessment:**

```
العنصر → suspicious 87%
Reason: "whale + element" is semantically incoherent
```

**Crop from the original image at the word's coordinates:**

```
┌─────────────┐
│  الْعَنْبَر  │
└─────────────┘
("ambergris" — the sperm whale)
```

**Visual verifier:**

```
العنبر → 98%
```

**Final output:**

```json
{
  "ocr": "العنصر",
  "verified": "العنبر",
  "context_score": 0.13,
  "visual_confidence": 0.98,
  "action": "AUTO_FIX"
}
```

---

## 4. Components in Detail

### 4.1 OCR Layer (LightOn)

It must produce output that **preserves coordinates**, not raw text. Without bounding boxes the whole approach collapses, because you can't crop the image.

Minimum per word:

```json
{
  "text": "العنصر",
  "page": 412,
  "line_id": 17,
  "bbox": [x, y, w, h],
  "ocr_confidence": 0.71
}
```

If the OCR emits per-word confidence, use it as an additional signal — a word with low confidence **and** an odd context is a very strong error candidate.

### 4.2 The Context Model

Runs on the extracted text, with a context window of roughly one sentence before and one after.

**Suspicion signals:**

- **High perplexity**: the word is statistically unexpected in its position.
- **Semantic mismatch**: "whale" + "element" — the two words don't belong to the same domain.
- **Out-of-vocabulary word** or an anomalous morphological form.
- **Consistency breaks**: a word appearing in two different forms within the same document.
- **Conflict with document type**: a medical term inside a jurisprudential text.

**Hard rule:** the Context Model returns only a `span` and a `score`. It must never return a replacement that gets written into the text directly.

### 4.3 The Visual Verifier

Receives an image cropped around the bbox, with **padding** of 15–25% around the word — the padding matters because diacritics and dots often fall outside the box.

**Practical implementation notes:**

- Send the model **the full line** rather than the isolated word where possible. Font and size context improve accuracy considerably.
- Constrain the output format: the word only, no explanation.
- If the visual model returns the same word as the OCR with high confidence, the context was odd but the text was right. Leave it.
- Run the model twice on differently sized crops; agreement between them is a strong confidence signal.

---

## 5. Decision Policy

Once the Visual Verifier responds, the decision comes from the intersection of both scores:

| context_score | visual_confidence | `action` |
|---|---|---|
| Low (< 0.20) | High (> 0.95) | `AUTO_FIX` — automatic correction |
| Low | Medium (0.75–0.95) | `SUGGEST` — shown to a human reviewer |
| Low | Low (< 0.75) | `FLAG` — marked for manual review |
| Medium | Any | `SUGGEST` |
| High (> 0.50) | — | `KEEP` — never sent to Vision at all |

**Always retain the original text** in a separate field. Any correction must be reversible, not a destructive overwrite.

### Suggested per-correction output

```json
{
  "doc_id": "ms-4417",
  "page": 412,
  "bbox": [318, 1094, 96, 41],
  "ocr": "العنصر",
  "verified": "العنبر",
  "context_score": 0.13,
  "visual_confidence": 0.98,
  "action": "AUTO_FIX",
  "verifier": "vlm-v2",
  "timestamp": "2026-08-11T09:14:22Z"
}
```

---

## 6. Operating Economics

Assumption: 50,000 pages × ~350 words = ~17.5M words.

| Stage | Processing volume | Notes |
|---|---|---|
| OCR | 100% of pages | One pass, unavoidable |
| Context Review | 100% of text | Text only, relatively low cost |
| Visual Verification | 1–4% of words | ~175K–700K crops |

Running Vision on every page costs **25–100× more** than the selective verification path. That gap is the entire point of this design.

**Further optimizations:**

- **Batching**: bundle 20–40 crops per request instead of one request per word.
- **Caching**: the same suspicious word recurs across a document. Store the verification result and reuse it as a prior.
- **Dictionary short-circuit**: once a suspicious word has been corrected to the same result 50 times, promote it to a substitution rule and skip the Vision calls.

---

## 7. Risks and Caveats

- **The source is wrong, not the OCR.** If the page itself contains a typo, the Visual Verifier will confirm the "error" because that is genuinely what's written. This is correct behavior — but the output must make clear it's a faithful transcription, not a correction.
- **Diacritics and hamzas.** A tight crop clips the marks. Adequate padding is non-negotiable for vocalized texts.
- **Imprecise bounding boxes.** If the OCR emits shifted coordinates, anchor the crop to the line instead of the word.
- **Linguistic bias in the Context Model.** These models skew toward contemporary Arabic, so they flag classical or rare vocabulary without cause. Calibrate the suspicion threshold on a sample of the same document type before a full run.
- **Drift across batches.** If the visual model version changes mid-project, log the version with every correction so you can audit the differences.

---

## 8. Suggested Rollout Plan

1. **Sample 200 pages**, varied (clean type / poor type / vocalized text).
2. Review them manually to build **ground truth**.
3. Run the full pipeline on them and measure: detection precision and recall, plus correction accuracy.
4. Calibrate the `context_score` and `visual_confidence` thresholds on those results.
5. Scale to 5,000 pages, manually review a random 2%.
6. Run the full 50,000 with a dashboard tracking the `FLAG` rate — a sudden spike means a problem in a specific batch of scans.

---

# 9. Approach 3 — OCR Ensemble

> Instead of Context + Vision Model, have multiple OCR engines read the suspicious regions and settle it by vote.

## 9.1 The Idea

```
LightOn:
العنصر

Second OCR:
العنبر

Third OCR:
العنبر
```

**Voting:**

```
العنبر = 2
العنصر = 1

→ العنبر
```

## 9.2 Architecture

```
LightOn
   ↓
Context / Error Detector
   ↓
Suspicious Region
   ↓
Crop
   ↓
┌────────────┬─────────────┐
↓            ↓             ↓
LightOn    PaddleOCR     Surya/other
↓            ↓             ↓
العنصر      العنبر         العنبر
        ↓
      Voting
        ↓
      العنبر
```

You do not run all 3 engines on the entire book. Only on:

```
Suspicious Crops
```

## 9.3 Advantage and Drawback

**Advantage:** no LLM relies on linguistic guessing for the final decision. Every vote comes from an actual visual read of the pixels, not from a linguistic probability.

**Drawback:** all three can fail on the same script. Especially when:

- Page quality is poor
- The typeface is unusual
- Diacritics overlap
- The image is low-resolution

---

## 9.4 Critical Point: Normalize Before Voting

This is the single biggest thing that breaks ensembles for Arabic. Engines render the same word in different forms, so the vote fragments for no real reason:

```
Engine A: العنبر
Engine B: العنبر      ← plain alif
Engine C: الْعَنْبَر    ← vocalized

Without normalization → 1 / 1 / 1  ❌ (full agreement that looks like disagreement)
```

**Normalize before tallying:**

| Case | Normalization |
|---|---|
| أ / إ / آ / ٱ | → ا |
| ة | → ه |
| ى | → ي |
| Diacritics and shadda | Stripped for comparison only |
| Tatweel (ــ) | Removed |
| Extra whitespace | Collapsed |

**Important:** normalization is for comparison only. Once you pick a winner, return the **original, un-normalized** form from the winning engine so you don't lose the diacritics.

---

## 9.5 Voting Mechanics

### Weighted voting

Engines aren't equally good on the same script. Calibrate weights on a ground-truth sample:

```
score(w) = Σ  weight(engine) × confidence(engine, w)
```

Example weights after calibration on an Arabic manuscript:

| Engine | Weight | Note |
|---|---|---|
| LightOn | 0.40 | Strong on printed text |
| Surya | 0.35 | Better on skewed lines |
| PaddleOCR | 0.25 | Relatively weak on vocalized text |

### Agreement level as a confidence signal

The voting pattern is itself information, not just a way to pick a winner:

| Pattern | Interpretation | Action |
|---|---|---|
| 3/3 agreement | Very high confidence | `AUTO_FIX` |
| 2/1 | Clear majority | `AUTO_FIX` if the majority's confidence > 0.85, else `SUGGEST` |
| 1/1/1 | Total disagreement — the region itself is poor | `ESCALATE` (VLM or human reviewer) |
| 3/3 but context still suspicious | The source itself likely contains the error | `KEEP` + flag |

That last row matters: unanimous agreement **combined with** persistent context suspicion is strong evidence the page really was written that way, not that the OCR failed.

### Tie-breaking

When a tie survives weighting, rank by:
1. The engine with the highest `confidence` on this specific crop.
2. The output that exists in the lexicon.
3. Consistency with the same word elsewhere in the same document.

---

## 9.6 The Correlated-Errors Problem

The drawback noted above is more dangerous than it looks, and deserves detail: **voting assumes voter independence, and that assumption is weak here.**

The engines share:
- Similar training data sources (the same public Arabic corpora)
- Convergent preprocessing steps (binarization, deskew)
- The same bias toward common typefaces

The result: when an image is poor, all three drift toward the same error, and the vote yields **3/3 with high confidence on a wrong answer** — which is worse than disagreement, because it sails through as `AUTO_FIX` with nobody suspicious of it.

**Mitigations:**

- **Diversify architecture, not count.** Two fundamentally different engines (CTC vs seq2seq/transformer) beat five similar ones.
- **Diversify inputs.** Send each engine a slightly different version of the crop (different padding, different contrast). Agreement across varied inputs is far stronger evidence than agreement on the identical input.
- **Build an image-quality gate.** Measure blur / DPI / noise ratio on the crop. Below threshold, **discard the vote result** regardless of agreement and route to review — because that's precisely the regime where errors correlate.
- **Monitor the 3/3 rate.** If it jumps suddenly in a batch, that signals a scanner problem, not improved accuracy.

---

## 9.7 Operational Considerations

- **Different cost type.** Ensemble cost is local compute (CPU/GPU on your infrastructure), not API calls. If you have the infrastructure, it's far cheaper than a VLM. If you don't, the gap narrows.
- **Crops are fully parallel.** Each crop is independent, so throughput is bound by worker count, not processing order.
- **Pin versions.** Every engine upgrade shifts its output distribution and invalidates the calibrated weights. Pin versions and log them with each correction.
- **Fault tolerance.** If an engine crashes or returns empty, the system must decide on two votes — record this in the output (`voters: 2`) so confidence is computed correctly.

### Suggested output

```json
{
  "doc_id": "ms-4417",
  "page": 412,
  "bbox": [318, 1094, 96, 41],
  "ocr": "العنصر",
  "votes": {
    "lighton":   { "text": "العنصر", "conf": 0.71 },
    "paddleocr": { "text": "العنبر", "conf": 0.88 },
    "surya":     { "text": "العنبر", "conf": 0.93 }
  },
  "normalized_tally": { "العنبر": 2, "العنصر": 1 },
  "verified": "العنبر",
  "agreement": "2/3",
  "weighted_score": 0.79,
  "image_quality": 0.84,
  "action": "AUTO_FIX"
}
```

---

# 10. Comparing the Two Approaches

| Criterion | Approach 2 (Context + VLM) | Approach 3 (OCR Ensemble) |
|---|---|---|
| **Source of final decision** | A single visual read | A vote among visual reads |
| **Linguistic-guessing risk** | Partially present (the VLM carries a language prior) | Nearly absent |
| **Performance on poor script** | Better — the VLM exploits wider visual context | Weaker — errors correlate |
| **Performance on manuscripts / rare text** | Better | Weaker (engines trained on modern print) |
| **Interpretability** | Moderate (one model's output) | High — you see who said what |
| **Cost type** | API calls per crop | Local compute |
| **Cost control** | Direct (number of calls) | Depends on your infrastructure |
| **Setup difficulty** | Lower — one component | Higher — 3 engines + normalization + weight calibration |
| **Worst-case failure** | Invents a linguistically plausible word | 3/3 agreement on a wrong answer with high confidence |

**Bottom line:** Approach 3 is more accurate on easy and medium cases and cheaper if you have the infrastructure. Approach 2 clearly wins on hard material — rare typefaces, manuscripts, vocalized text, damaged pages.

---

# 11. The Hybrid (Recommended)

The two approaches aren't alternatives; they're a natural escalation ladder. Order them cheapest to most expensive:

```
50,000 pages
     ↓
LightOn
     ↓
Context / Error Detector
     ↓
Suspicious Crops                    (~2%)
     ↓
OCR Ensemble  ←──────────── cheap layer
     ↓
┌──────────────┬────────────────────┐
│ 3/3 agreement │  2/1  or  1/1/1     │
│ + acceptable  │  or poor image      │
│   image quality│  quality           │
↓              ↓
AUTO_FIX      VLM Verification  ←── expensive layer (~0.2%)
                    ↓
              ┌─────┴──────┐
              ↓            ↓
          AUTO_FIX      HUMAN REVIEW
```

**The logic:** the ensemble settles the overwhelming majority of cases at local-compute cost. The VLM fires only on the cases where the ensemble itself declared uncertainty — which are precisely the cases the VLM handles best.

Also: **engine disagreement is the best VLM trigger you have.** It's more precise than the Context Model's suspicion score alone, because it's a visual signal rather than a linguistic one.

**One important addition:** add a reverse path — if the ensemble agrees 3/3 but the Context Model remains strongly suspicious (`score < 0.10`), send it to the VLM anyway. This case catches exactly the correlated-error scenario that voting is blind to.

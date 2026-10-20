# Plan: correction evidence record

**Spec:** [spec.md](spec.md)

## Mapping walidapro `SuspiciousWord` → `Evidence`

| SuspiciousWord | Evidence |
|---|---|
| `word` | `original` |
| `candidate` | `candidate` |
| `sentence`, `reason`, `score` | `detector_detail.{sentence,reason}`, `scores.suspicion` |
| `bbox`, `bbox_text`, `bbox_match_score` | `bbox`, `reads.paddle`, `scores.bbox_match` |
| `word_crop`, `context_crop` | `crop_ref.{word,context}` |
| `visual_reading`, `visual_score` | `reads.lighton_variants`, `scores.visual` |
| `gemini_visual_reading`, `gemini_visual_confidence`, `gemini_visual_unclear` | `reads.blind`, `scores.blind_confidence`, `reads.blind_unclear` |
| `gemini_supports_original/candidate` | `scores.support_{original,candidate}` |
| `context_score` | `scores.context` |
| `final_score`, `status` | `scores.final`, `status` |

## Files

```
specs/018-correction-evidence-record/contracts/evidence.schema.json   new
tools/apply_corrections.py, replace_page.py, apply_context_suggestions.py   write evidence
tools/build_review.py                                                     show it
service/ai_engine/domain/evidence.py                                     dataclass (stdlib), used by 022
```

# Reference transcripts

Hand-made, page-by-page transcripts that `tools/score_corrections.py` (spec 019) scores the
pipeline against. They are the only ground truth in the project: the fast-vs-verified
decision (spec 026) is made on these numbers.

## Layout

```text
references/<book>/p001.txt
references/<book>/p002.txt
...
```

- `<book>` is free-form; you pass the directory to the tool.
- File names match the page records: `p` + 3-digit page number + `.txt`.
- Pages without a file are not scored. Only transcribe the pages you need.

## How to transcribe

1. Type exactly what is **printed** on the page, in reading order, from the page image, not
   from any OCR output. Copying OCR into a reference hides exactly the errors being measured.
2. Words separated by single spaces; line breaks don't matter.
3. **Diacritics:** if the book prints tashkeel, include it exactly as printed (that's what
   DER is scored on). If it doesn't, don't add any.
4. Keep hamza and alef forms as printed (`إلى`, not `الى`). The scorer does not fold them,
   because a lost hamza is a real error (`مريي` / `مرئي`).
5. Punctuation is optional; the scorer ignores it.
6. Leave out running headers, page numbers and decorative text if the pipeline is not
   supposed to read them.
7. Note anything ambiguous (an unclear print, a smudge) in `references/<book>/NOTES.md`.

## Wanted (spec 019)

| Book | Pages | Why | Status |
|---|---|---|---|
| Ibn Battuta in Egypt | 5 | already transcribed once (538 words, used for the July 2026 OCR comparison), but **the transcript file isn't in the repo**. Only its scores are, in `models/ibn_battuta/OCR model comparison.html` (git history). Whoever made it: please add it here | missing |
| الشمس | 16 | the 10 known letter substitutions (`الفضل`/`الفصل`, `فزح`/`قزح`, …) from the README | to do |
| one undiacritised book | any | exercises the diacritise path | to do |

## Score a book

```bash
python3 tools/score_corrections.py "work/<book>-lighton" references/<book>
python3 tools/score_corrections.py "work/<book>-lighton" references/<book> --json
```

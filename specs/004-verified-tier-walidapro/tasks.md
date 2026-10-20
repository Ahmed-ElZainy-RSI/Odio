# Tasks: Verified Tier (epic)

This epic is delivered as separate features. Each has its own `spec.md`, `plan.md` and `tasks.md`.

| Order | Feature | What it delivers |
|---|---|---|
| 1 | [005-walidapro-recovery](../005-walidapro-recovery/) | Waleed's missing files, baseline test run |
| 1 | [006-letter-preservation-guard](../006-letter-preservation-guard/) | letters-unchanged check, both tiers (can start now) |
| 1 | [007-reraise-programming-errors](../007-reraise-programming-errors/) | bugs never look like provider failures |
| 2 | [018-correction-evidence-record](../018-correction-evidence-record/) | one evidence shape for every fix |
| 2 | [020-page-verifier-port](../020-page-verifier-port/) | the port + tier routing (doesn't exist yet) |
| 2 | [021-provider-call-cache](../021-provider-call-cache/) | fingerprinted cache + atomic writes |
| 3 | [022-verified-ocr](../022-verified-ocr/) | blind-crop verifier on token ids |
| 3 | [023-visual-diacritics](../023-visual-diacritics/) | per-word printed marks, parallelized |
| 3 | [024-final-context-review](../024-final-context-review/) | opt-in, suggestions only |
| 3 | [030-contract-replay-walidapro-tests](../030-contract-replay-walidapro-tests/) | Waleed's contract scenarios vs our service |
| 3 | [032-tts-chunking-dual-narrator](../032-tts-chunking-dual-narrator/) | chunker, optional second voice |
| 4 | [025-cost-metering](../025-cost-metering/) + [019-correction-accuracy-harness](../019-correction-accuracy-harness/) | the numbers |
| 5 | [026-tier-benchmark](../026-tier-benchmark/) | decides Q-07 / ADR-009 |
| 6 | [033-retire-walidapro-duplicates](../033-retire-walidapro-duplicates/) | one implementation of everything |

Epic done when 026 has recorded a decision and 033 is merged (or 026 decided "no", and 033
archives walidapro without porting the rest).

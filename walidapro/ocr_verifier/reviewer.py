from __future__ import annotations

import time
import random
import json
import re
import requests

from .config import SETTINGS
from .models import SuspiciousWord
from .utils import extract_json_object, split_sentences


class ContextReviewer:
    """
    Gemini-based Arabic OCR Context Reviewer.

    دوره:
    1. فحص النص كاملًا.
    2. اكتشاف الكلمات المشكوك فيها.
    3. اقتراح candidate عند وجود دليل معقول.
    4. مقارنة candidate مع السياق والقراءة البصرية.
    5. إعادة التحقق قبل قبول التصحيح النهائي.
    """

    def __init__(self) -> None:
        if not SETTINGS.reviewer_model:
            raise RuntimeError(
                "REVIEWER_MODEL is empty. "
                "Set REVIEWER_MODEL to a language model capable of Arabic reasoning."
            )

        if not SETTINGS.reviewer_api_key:
            raise RuntimeError(
                "REVIEWER_API_KEY is empty."
            )

        self.session = requests.Session()

        self.session.headers.update({
            "Authorization": f"Bearer {SETTINGS.reviewer_api_key}",
            "Content-Type": "application/json",
        })

    # ============================================================
    # Generic JSON chat request
    # ============================================================

    def _chat_json(
        self,
        system: str,
        user: str,
        max_tokens: int = 5000,
    ) -> dict:
        """
        Send a reviewer request and return a JSON object.

        Safety / cost policy:
        - Maximum 3 attempts.
        - Retry HTTP/network failures.
        - If the model returns malformed JSON, try to repair/extract it locally
          before spending another API call.
        - Never silently convert a failed review into "0 suspicious words".
        """

        payload = {
            "model": SETTINGS.reviewer_model,
            "messages": [
                {
                    "role": "system",
                    "content": system,
                },
                {
                    "role": "user",
                    "content": user,
                },
            ],
            "temperature": 0.0,
            "max_tokens": max_tokens,
        }

        # Respect configured retries but cap cost at 3 attempts.
        configured_retries = int(
            getattr(SETTINGS, "retries", 3) or 3
        )
        max_attempts = max(
            1,
            min(configured_retries, 3),
        )

        configured_timeout = float(
            getattr(SETTINGS, "timeout", 180) or 180
        )

        timeout = (
            15.0,
            min(configured_timeout, 90.0),
        )

        retryable_statuses = {
            408,
            429,
            500,
            502,
            503,
            504,
        }

        last_error: Exception | None = None

        def parse_model_json(raw_text: str) -> dict:
            """
            Parse JSON conservatively.

            We only repair transport/formatting noise:
            - ```json fences
            - leading/trailing prose around the JSON object
            - trailing commas before } or ]

            We do NOT invent missing fields or OCR results.
            """

            cleaned = (raw_text or "").strip()

            if not cleaned:
                raise ValueError(
                    "Reviewer returned empty content."
                )

            # Remove Markdown fences if the model ignored the instruction.
            cleaned = re.sub(
                r"^\s*```(?:json)?\s*",
                "",
                cleaned,
                flags=re.IGNORECASE,
            )
            cleaned = re.sub(
                r"\s*```\s*$",
                "",
                cleaned,
            )

            # First use the project's existing extractor.
            try:
                parsed = extract_json_object(cleaned)

                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

            # Extract the outermost JSON object.
            first = cleaned.find("{")
            last = cleaned.rfind("}")

            if first == -1 or last == -1 or last <= first:
                raise ValueError(
                    "Reviewer response contains no complete JSON object."
                )

            candidate = cleaned[first:last + 1]

            # Safe formatting repair only: {"x": 1,} -> {"x": 1}
            candidate = re.sub(
                r",\s*([}\]])",
                r"\1",
                candidate,
            )

            parsed = json.loads(candidate)

            if not isinstance(parsed, dict):
                raise ValueError(
                    "Reviewer JSON root must be an object."
                )

            return parsed

        for attempt in range(
            1,
            max_attempts + 1,
        ):
            try:
                response = self.session.post(
                    f"{SETTINGS.reviewer_base_url.rstrip('/')}/chat/completions",
                    json=payload,
                    timeout=timeout,
                )

                status_code = response.status_code

                if status_code in retryable_statuses:
                    message = response.text[:700]

                    raise requests.HTTPError(
                        f"HTTP {status_code}: {message}",
                        response=response,
                    )

                response.raise_for_status()

                data = response.json()

                choices = data.get("choices") or []

                if not choices:
                    raise RuntimeError(
                        "Reviewer returned no choices."
                    )

                message = (
                    choices[0].get("message")
                    or {}
                )

                raw_text = message.get("content")

                if not raw_text:
                    raise RuntimeError(
                        "Reviewer returned empty content."
                    )

                return parse_model_json(
                    str(raw_text)
                )

            except requests.HTTPError as exc:
                last_error = exc

                status_code = None

                if exc.response is not None:
                    status_code = (
                        exc.response.status_code
                    )

                # Permanent client/configuration errors:
                # another attempt will not fix them.
                if (
                    status_code is not None
                    and 400 <= status_code < 500
                    and status_code not in retryable_statuses
                ):
                    raise RuntimeError(
                        "Reviewer request failed with "
                        f"non-retryable HTTP {status_code}: {exc}"
                    ) from exc

            except (
                requests.ConnectionError,
                requests.Timeout,
                requests.JSONDecodeError,
                json.JSONDecodeError,
                ValueError,
                KeyError,
                IndexError,
                RuntimeError,
            ) as exc:
                last_error = exc

            if attempt >= max_attempts:
                break

            # Short bounded backoff. Avoid 16/32-second waits and
            # unnecessary API spend on a single page.
            wait_seconds = min(
                1.5 * (2 ** (attempt - 1))
                + random.uniform(0.2, 0.8),
                5.0,
            )

            print(
                f"[REVIEWER RETRY] "
                f"{attempt}/{max_attempts} failed: "
                f"{last_error}"
            )

            print(
                f"[REVIEWER RETRY] "
                f"Waiting {wait_seconds:.1f}s "
                f"before next attempt..."
            )

            time.sleep(
                wait_seconds
            )

        raise RuntimeError(
            "Reviewer unavailable after "
            f"{max_attempts} attempts. "
            "Initial review status must be treated as unavailable, "
            "not as zero suspicious words. "
            f"Last error: {last_error}"
        )

    # ============================================================
    # STEP 1
    # Find suspicious OCR words
    # ============================================================

    def find_suspicious(
        self,
        page: int,
        text: str,
    ) -> list[SuspiciousWord]:

        sentences = split_sentences(text)

        if not sentences:
            return []

        system = """
أنت نظام متخصص في المراجعة اللغوية والسياقية العميقة للنصوص العربية المستخرجة بواسطة OCR من صفحات كتب مطبوعة.

مهمتك الأساسية هي اكتشاف الكلمات أو المقاطع التي يوجد احتمال معقول أن نظام OCR قرأها بصورة غير صحيحة.

هذه ليست مهمة تدقيق إملائي تقليدي.

لا تفترض أن الكلمة صحيحة لمجرد أنها:
- موجودة في القاموس.
- كلمة عربية صحيحة.
- صحيحة إملائياً.
- ممكنة صرفياً.

الكلمة قد تكون عربية صحيحة تماماً ومع ذلك تكون ناتجة عن خطأ OCR إذا كانت غير مناسبة للسياق الحقيقي.

يجب أن تفهم النص كاملاً قبل إصدار الأحكام على الكلمات.

نفّذ داخلياً مراجعة متعددة المراحل.

المرحلة الأولى:
افهم الموضوع العام للنص والفقرة.

المرحلة الثانية:
راجع كل جملة من ناحية:
- المعنى.
- السياق.
- النحو.
- الصرف.
- العلاقات بين الكلمات.
- توافق الفعل والفاعل.
- المفرد والجمع.
- المذكر والمؤنث.
- حروف الجر.
- أدوات الربط.
- التعريف والتنكير.

المرحلة الثالثة:
راجع كل كلمة داخل الجملة بعد فهم السياق.

المرحلة الرابعة:
حلل احتمال أن تكون المشكلة ناتجة عن OCR بصري.

افحص احتمالات مثل:
- استبدال حرف بحرف.
- اختلاف أو سقوط النقاط.
- إضافة نقطة.
- سقوط حرف.
- إضافة حرف.
- دمج حروف.
- فصل حروف.
- دمج كلمتين.
- تقسيم كلمة واحدة.
- التباس أشكال الحروف العربية.
- أخطاء الهمزات.
- أخطاء الألف والياء والألف المقصورة.
- أخطاء الهاء والتاء المربوطة.
- أخطاء التشكيل.
- أخطاء ناتجة عن اتصال الحروف.
- أخطاء ناتجة عن علامات الترقيم.
- أخطاء ناتجة عن جودة الطباعة أو الخط.

لا تعتمد على قائمة أخطاء ثابتة.

لا تعتمد على أمثلة محفوظة.

استنتج الخطأ المحتمل من النص الحالي فقط.

المرحلة الخامسة:
راجع الدلالة.

ابحث بشكل خاص عن الكلمات التي تبدو صحيحة إملائياً لكنها:
- غير طبيعية في الجملة.
- تجعل المعنى غير منطقي.
- لا تتوافق مع موضوع الفقرة.
- لا تتوافق مع الكلمات السابقة أو التالية.
- يوجد احتمال أن OCR أنتج كلمة مختلفة عن الأصل المطبوع.

المرحلة السادسة:
استخدم سياق الفقرة عند الحاجة، وليس الجملة وحدها.

المرحلة السابعة:
بعد انتهاء المراجعة الأولى، نفّذ مراجعة ثانية مستقلة للنص بحثاً عن أخطاء فاتتك في المرور الأول.

ركز في المراجعة الثانية على:
- الكلمات الصحيحة إملائياً والخاطئة سياقياً.
- الكلمات غير الطبيعية دلالياً.
- الأخطاء البصرية الدقيقة.
- الكلمات المدمجة.
- الكلمات المنقسمة.
- أخطاء OCR التي قد لا يكتشفها Spell Checker.

مبدأ مهم جداً:

Detection Stage يجب أن يكون حساساً نسبياً.

هدفك هنا هو High Recall:
اكتشاف الحالات التي تستحق التحقق البصري لاحقاً.

لكن لا تعتبر كل كلمة نادرة خطأ.

النص قد يحتوي على:
- أسماء علم.
- أسماء أشخاص.
- أسماء أماكن.
- أسماء حيوانات.
- أسماء نباتات.
- مصطلحات علمية.
- كلمات قديمة.
- كلمات أجنبية.
- تعبيرات خاصة بالمؤلف.

لا تعتبرها أخطاء بدون دليل سياقي أو بصري معقول.

لا تحسن أسلوب المؤلف.

لا تعيد صياغة النص.

لا تغير كلمة إلى مرادف أجمل.

لا تصحح معلومة علمية.

لا تضف معلومات من معرفتك.

لا تحذف محتوى.

مهمتك الوحيدة هي تحديد الأماكن التي يحتمل أن النص المستخرج فيها لا يطابق النص الأصلي المطبوع.

عندما تحدد كلمة مشكوك فيها:

أعط score من 0.0 إلى 1.0 يعبر عن احتمال وجود خطأ OCR.

الدرجة يجب أن تعتمد على:
- الدليل السياقي.
- الدليل الدلالي.
- الدليل النحوي.
- الدليل الصرفي.
- احتمالية الخطأ البصري.
- مدى طبيعية الكلمة الحالية.
- قوة البديل المحتمل.

يمكنك اقتراح candidate عندما يكون هناك بديل واضح ومعقول.

لكن لا تخترع candidate.

إذا لم يوجد بديل واضح:
candidate = null

candidate يجب أن يمثل محاولة لاستعادة النص الأصلي المحتمل، وليس إعادة كتابة الجملة.

سبب الاشتباه يجب أن يكون مختصراً ودقيقاً.

لا تستخدم أسباباً عامة أو غامضة.

أرجع JSON صالح فقط.

ممنوع Markdown.

ممنوع أي نص قبل JSON.

ممنوع أي نص بعد JSON.

قواعد JSON الإلزامية:
- استخدم علامات اقتباس مزدوجة " فقط لمفاتيح JSON والنصوص.
- لا تضع فاصلة بعد آخر عنصر في items.
- إذا لم تجد أي حالة مشكوك فيها أرجع:
  {"total_words": 0, "items": []}
- تأكد داخلياً أن JSON صالح قبل إرساله.
""".strip()

        user = f"""
راجع النص التالي بالكامل باعتباره ناتج OCR.

الصفحة رقم:
{page}

الحد الأدنى للاشتباه المستخدم بواسطة النظام:
{SETTINGS.suspicion_threshold}

لا تجعل هذا الحد يمنعك من المراجعة العميقة.
أعط الدرجة الحقيقية لكل حالة، وسيقوم البرنامج بفلترة النتائج لاحقاً.

أرجع النتيجة بالصيغة التالية فقط:

{{
  "total_words": 0,
  "items": [
    {{
      "word": "الكلمة كما ظهرت حرفياً في النص",
      "sentence": "الجملة الأصلية التي تحتوي عليها كما ظهرت في النص",
      "score": 0.0,
      "reason": "سبب محدد للاشتباه",
      "candidate": null
    }}
  ]
}}

قواعد إلزامية:

- word يجب أن يكون موجوداً حرفياً في النص.
- sentence يجب أن تكون مأخوذة من النص نفسه.
- لا تعدل sentence.
- لا تضف كلمة غير موجودة.
- لا تغير التشكيل فقط وتعتبره تصحيحاً.
- لا تعتبر اختلافاً أسلوبياً خطأ OCR.
- لا تعتبر كلمة صحيحة فقط لأنها موجودة في القاموس.
- لا تفترض أن candidate صحيح قبل تحليل original.
- يجب أولاً اكتشاف وجود خلل في original.
- بعد ذلك فقط يمكن اقتراح candidate.
- candidate يفضل أن يكون كلمة واحدة.
- إذا لم يوجد candidate واضح استخدم null.
- افحص النص مرة أولى.
- ثم أعد فحصه مرة ثانية بحثاً عن الأخطاء غير الواضحة.
- لا تخرج إلا الحالات التي تستحق فعلاً الانتقال إلى التحقق البصري.

النص:

{text}
""".strip()

        data = self._chat_json(
            system,
            user,
            max_tokens=6000,
        )

        items: list[SuspiciousWord] = []

        for raw in data.get("items", []):
            try:
                score = float(
                    raw.get(
                        "score",
                        0.0,
                    )
                )

                word = str(
                    raw.get(
                        "word",
                        "",
                    )
                ).strip()

                sentence = str(
                    raw.get(
                        "sentence",
                        "",
                    )
                ).strip()

                # Basic validation
                if not word:
                    continue

                if not sentence:
                    continue

                # مهم:
                # الكلمة يجب أن تكون موجودة فعلياً في النص
                if word not in text:
                    continue

                # الجملة يجب أن تحتوي الكلمة
                if word not in sentence:
                    continue

                # فلترة threshold بعد مراجعة Gemini
                if score < SETTINGS.suspicion_threshold:
                    continue

                candidate = raw.get(
                    "candidate"
                )

                if candidate is not None:
                    candidate = str(
                        candidate
                    ).strip()

                    if (
                        not candidate
                        or candidate.lower() == "null"
                    ):
                        candidate = None

                reason = str(
                    raw.get(
                        "reason",
                        "context anomaly",
                    )
                ).strip()

                items.append(
                    SuspiciousWord(
                        page=page,
                        word=word,
                        sentence=sentence,
                        score=max(
                            0.0,
                            min(
                                1.0,
                                score,
                            ),
                        ),
                        reason=reason,
                        candidate=candidate,
                    )
                )

            except (
                TypeError,
                ValueError,
            ):
                continue

        return items

    # ============================================================
    # STEP 2
    # Compare candidate against context + visual reading
    # ============================================================

    def compare_candidate(
        self,
        sentence: str,
        original: str,
        candidate: str,
        visual_reading: str,
    ) -> float:

        system = """
أنت محكم متخصص في التحقق من تصحيحات OCR العربية.

هذه مرحلة تحقق وليست مرحلة كتابة أو تحسين أسلوب.

مهمتك تقييم ما إذا كان candidate يمثل على الأرجح الكلمة الأصلية المطبوعة التي أخطأ OCR في قراءتها.

يجب أن تستخدم الأدلة التالية:

1. الكلمة الأصلية الناتجة عن OCR.
2. candidate المقترح.
3. السياق الكامل للجملة.
4. القراءة البصرية المستخرجة من crop للكلمة.
5. احتمالية وجود خطأ بصري بين original وcandidate.
6. صحة candidate داخل السياق.
7. مدى توافق القراءة البصرية مع candidate.

لا تقبل candidate لمجرد أنه يجعل الجملة أجمل.

لا تقبل candidate لأنه أكثر شيوعاً.

لا تعيد صياغة النص.

لا تستخدم معرفتك لتغيير محتوى المؤلف.

هدفك هو استعادة النص الأصلي المحتمل.

إذا كانت الأدلة غير كافية، أعط score منخفضاً.

إذا تعارض السياق مع القراءة البصرية، كن متحفظاً.

إذا كانت القراءة البصرية تحتوي على نص إضافي بسبب اتساع crop، ركز على الجزء الأقرب للكلمة محل التحقق.

أرجع JSON فقط بدون Markdown.
""".strip()

        user = f"""
قيّم التصحيح التالي:

الجملة:
{sentence}

الكلمة الناتجة من OCR:
{original}

candidate:
{candidate}

القراءة البصرية من صورة الكلمة:
{visual_reading}

أعط احتمالاً من 0.0 إلى 1.0 أن candidate هو التصحيح الصحيح للكلمة الأصلية.

أرجع فقط:

{{
  "score": 0.0
}}
""".strip()

        data = self._chat_json(
            system,
            user,
            max_tokens=1000,
        )

        try:
            score = float(
                data.get(
                    "score",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            score = 0.0

        return max(
            0.0,
            min(
                1.0,
                score,
            ),
        )

    # ============================================================
    # STEP 3
    # Final validation before modifying OCR text
    # ============================================================

    def validate_replacement(
        self,
        old_sentence: str,
        new_sentence: str,
        original: str,
        candidate: str,
    ) -> float:

        system = """
أنت آخر طبقة تحقق قبل تعديل نص OCR العربي.

هذه المرحلة يجب أن تكون محافظة جداً.

هدفك منع التصحيحات الخاطئة.

قيّم فقط هل استبدال original بـ candidate يعكس على الأرجح النص الأصلي المطبوع.

لا تحكم على جمال الأسلوب.

لا تحسن الجملة.

لا تستخدم مرادفات.

لا تغير معلومات المؤلف.

لا تفترض أن الجملة الجديدة أفضل لمجرد أنها أكثر سلاسة.

قارن الجملة قبل وبعد التغيير.

تحقق من:

- السياق.
- المعنى.
- النحو.
- الصرف.
- طبيعة التغيير.
- احتمال أن يكون الاختلاف ناتجاً فعلاً عن OCR.
- هل التغيير موضعي ومبرر.
- هل candidate يحل خللاً حقيقياً موجوداً في original.

إذا كان هناك أي شك مهم، قلل score.

هدف هذه المرحلة هو Precision مرتفع.

أرجع JSON فقط.
""".strip()

        user = f"""
راجع التصحيح النهائي التالي.

قبل التصحيح:
{old_sentence}

بعد التصحيح:
{new_sentence}

الكلمة الأصلية:
{original}

المرشح:
{candidate}

أعط confidence من 0.0 إلى 1.0 لقبول هذا التصحيح تلقائياً.

أرجع فقط:

{{
  "score": 0.0
}}
""".strip()

        data = self._chat_json(
            system,
            user,
            max_tokens=1000,
        )

        try:
            score = float(
                data.get(
                    "score",
                    0.0,
                )
            )

        except (
            TypeError,
            ValueError,
        ):
            score = 0.0

        return max(
            0.0,
            min(
                1.0,
                score,
            ),
        )
from __future__ import annotations

import base64
import os
import re
import time
import wave
from pathlib import Path

from dotenv import load_dotenv
from google import genai


load_dotenv()


class GeminiTTS:
    """
    Gemini TTS engine for Arabic children's books.

    Project:
        UAE Arabic children's educational reading.

    Voices:
        Female -> Leda
        Male   -> Orus

    Output:
        WAV
        24 kHz
        Mono
        16-bit PCM
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        voice: str | None = None,
        narrator_type: str = "female",
        narrator: str | None = None,
    ) -> None:

        # ====================================================
        # API KEY
        # ====================================================

        self.api_key = (
            api_key
            or os.getenv("GEMINI_TTS_API_KEY")
            or os.getenv("REVIEWER_API_KEY")
        )

        if not self.api_key:
            raise RuntimeError(
                "GEMINI_TTS_API_KEY is empty."
            )

        # ====================================================
        # MODEL
        # ====================================================

        self.model = (
            model
            or os.getenv(
                "GEMINI_TTS_MODEL",
                "gemini-3.1-flash-tts-preview",
            )
        )

        # ====================================================
        # NARRATOR
        # ====================================================
        #
        # ندعم الاسمين:
        #
        # narrator_type="male"
        # narrator="male"
        #
        # حتى يظل الملف متوافقًا مع أي كود قديم.
        # ====================================================

        selected_narrator = (
            narrator
            if narrator is not None
            else narrator_type
        )

        selected_narrator = (
            selected_narrator
            or "female"
        ).strip().lower()

        if selected_narrator not in {
            "female",
            "male",
        }:
            raise ValueError(
                "narrator_type must be "
                "'female' or 'male'."
            )

        self.narrator = selected_narrator
        self.narrator_type = selected_narrator

        # ====================================================
        # VOICE
        # ====================================================

        if voice:

            self.voice = voice

        elif self.narrator == "male":

            self.voice = os.getenv(
                "GEMINI_TTS_MALE_VOICE",
                "Orus",
            )

        else:

            self.voice = os.getenv(
                "GEMINI_TTS_FEMALE_VOICE",
                os.getenv(
                    "GEMINI_TTS_VOICE",
                    "Leda",
                ),
            )

        # ====================================================
        # CLIENT
        # ====================================================

        self.client = genai.Client(
            api_key=self.api_key
        )

    # ========================================================
    # TTS TEXT CLEANING
    # ========================================================

    @staticmethod
    def clean_text_for_tts(
        text: str,
    ) -> str:
        """
        تنظيف نسخة النص التي ستدخل إلى TTS فقط.

        لا نغير:
            OCR
            Final corrected text
            Diacritized text

        الهدف:
            إزالة Formatting غير المناسب للصوت
            بدون تغيير كلمات الكتاب أو التشكيل.
        """

        if not text:
            return ""

        text = text.strip()

        # ====================================================
        # NORMALIZE NEWLINES
        # ====================================================

        text = text.replace(
            "\r\n",
            "\n",
        )

        text = text.replace(
            "\r",
            "\n",
        )

        # ====================================================
        # FIX SEPARATE DOT LINES
        #
        # مثال:
        #
        # وَقَالَتْ.
        # .
        # .
        #
        # يصبح:
        #
        # وَقَالَتْ...
        # ====================================================

        text = re.sub(
            r"\.(?:[ \t]*\n[ \t]*\.){1,}",
            "...",
            text,
        )

        # ====================================================
        # REMOVE REMAINING SINGLE-DOT LINES
        # ====================================================

        text = re.sub(
            r"(?m)^[ \t]*\.[ \t]*$",
            "",
            text,
        )

        # ====================================================
        # NORMALIZE TOO MANY DOTS
        # ====================================================

        text = re.sub(
            r"\.{4,}",
            "...",
            text,
        )

        # ====================================================
        # REMOVE SPACES BEFORE PUNCTUATION
        # ====================================================

        text = re.sub(
            r"[ \t]+([،؛:؟!,.])",
            r"\1",
            text,
        )

        # ====================================================
        # REMOVE TRAILING SPACES
        # ====================================================

        text = re.sub(
            r"[ \t]+\n",
            "\n",
            text,
        )

        # ====================================================
        # COLLAPSE EXCESSIVE EMPTY LINES
        # ====================================================

        text = re.sub(
            r"\n[ \t]*\n(?:[ \t]*\n)+",
            "\n\n",
            text,
        )

        return text.strip()

    # ========================================================
    # FEMALE PROMPT - LEDA
    # ========================================================

    @staticmethod
    def build_female_prompt(
        text: str,
    ) -> str:

        return f"""
اقرئي النص التالي كراوية عربية محترفة لكتاب أطفال تعليمي موجه للأطفال في دولة الإمارات العربية المتحدة.

الجمهور المستهدف أطفال يتعلمون اللغة العربية، لذلك يجب أن تكون القراءة واضحة جدًا، فصيحة، طبيعية، ممتعة، ومناسبة للتعلم والاستماع.

تعليمات الأداء الصوتي:

- استخدمي صوتًا أنثويًا دافئًا وحنونًا وواثقًا.
- تحدثي باللغة العربية الفصحى السليمة والواضحة.
- اجعلي مخارج الحروف واضحة جدًا.
- التزمي بالتشكيل الموجود في النص قدر الإمكان.
- لا تسرعي في القراءة.
- استخدمي سرعة متوسطة مائلة قليلًا إلى الهدوء.
- أعطي كل كلمة حقها في النطق.
- اجعلي القراءة قصصية وتعليمية في الوقت نفسه.
- اجعلي المصطلحات العلمية واضحة وسهلة السماع.
- استخدمي وقفات طبيعية عند الفواصل والنقاط.
- استخدمي نبرة سؤال طبيعية عند علامة الاستفهام.
- استخدمي حماسًا لطيفًا عند علامة التعجب.
- عند وجود حوار، اجعليه حيًا وطبيعيًا.
- لا تبالغي في تقليد الشخصيات.
- لا تستخدمي أداءً كرتونيًا مبالغًا فيه.
- حافظي على شخصية الراوية الأساسية طوال القراءة.
- لا تستخدمي نبرة إعلان.
- لا تستخدمي نبرة نشرة أخبار.
- اجعلي الطفل يشعر أنه يستمع إلى راوية قصة تعليمية محترفة.

الهدف:

تعليم الطفل اللغة العربية من خلال الاستماع إلى نطق عربي فصيح، واضح، مريح، وجذاب.

قواعد إلزامية:

- لا تغيري أي كلمة.
- لا تحذفي أي كلمة.
- لا تضيفي أي كلمة.
- لا تعيدي صياغة أي جملة.
- لا تشرحي النص.
- لا تقرئي التعليمات.
- اقرئي النص فقط.

النص:

{text}
""".strip()

    # ========================================================
    # MALE PROMPT - ORUS
    # ========================================================

    @staticmethod
    def build_male_prompt(
        text: str,
    ) -> str:

        return f"""
اقرأ النص التالي كراوٍ عربي محترف لكتاب أطفال تعليمي موجه للأطفال في دولة الإمارات العربية المتحدة.

الجمهور المستهدف أطفال يتعلمون اللغة العربية، لذلك يجب أن تكون القراءة واضحة جدًا، فصيحة، طبيعية، ودودة، وممتعة.

تعليمات الأداء الصوتي:

- استخدم صوتًا رجاليًا دافئًا وودودًا ومطمئنًا.
- تحدث باللغة العربية الفصحى السليمة والواضحة.
- اجعل مخارج الحروف واضحة جدًا.
- التزم بالتشكيل الموجود في النص قدر الإمكان.
- لا تتحدث بسرعة.
- استخدم سرعة متوسطة مائلة قليلًا إلى الهدوء.
- أعط كل كلمة حقها الكامل في النطق.
- اقرأ بأسلوب راوي قصص أطفال محترف.
- اجعل الأداء قصصيًا وتعليميًا وليس رسميًا جامدًا.
- اجعل المصطلحات العلمية واضحة وسهلة السماع.
- استخدم وقفات طبيعية ومريحة بين الجمل.
- أعط علامات الاستفهام نبرة سؤال طبيعية.
- أعط علامات التعجب حماسًا لطيفًا.
- عند وجود حوار، غيّر الإحساس بشكل بسيط وطبيعي.
- لا تقلد أصواتًا كرتونية.
- لا تبالغ في التمثيل.
- حافظ على شخصية الراوي الأساسية طوال القراءة.
- اجعل الصوت قريبًا وودودًا للطفل.
- لا تستخدم نبرة مذيع أخبار.
- لا تستخدم نبرة إعلانية.
- لا تجعل القراءة رسمية أكثر من اللازم.

الهدف:

تعليم الطفل اللغة العربية من خلال الاستماع إلى نطق عربي فصيح، واضح، مريح، وجذاب.

قواعد إلزامية:

- لا تغير أي كلمة.
- لا تحذف أي كلمة.
- لا تضف أي كلمة.
- لا تعد صياغة أي جملة.
- لا تشرح النص.
- لا تقرأ التعليمات.
- اقرأ النص فقط.

النص:

{text}
""".strip()

    # ========================================================
    # BUILD PROMPT
    # ========================================================

    def build_prompt(
        self,
        text: str,
    ) -> str:

        if self.narrator == "male":

            return self.build_male_prompt(
                text
            )

        return self.build_female_prompt(
            text
        )

    # ========================================================
    # SAVE WAV
    # ========================================================

    @staticmethod
    def save_wav(
        path: Path,
        pcm_data: bytes,
        channels: int = 1,
        rate: int = 24000,
        sample_width: int = 2,
    ) -> None:

        path = Path(
            path
        )

        path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        with wave.open(
            str(path),
            "wb",
        ) as wf:

            wf.setnchannels(
                channels
            )

            wf.setsampwidth(
                sample_width
            )

            wf.setframerate(
                rate
            )

            wf.writeframes(
                pcm_data
            )

    # ========================================================
    # GENERATE
    # ========================================================

    def generate(
        self,
        text: str,
        output_path: Path,
        retries: int = 3,
    ) -> Path:

        if not text:

            raise ValueError(
                "TTS text is empty."
            )

        # ====================================================
        # CLEAN COPY FOR TTS ONLY
        # ====================================================

        original_text = text

        text = self.clean_text_for_tts(
            text
        )

        if not text:

            raise ValueError(
                "TTS text contains only whitespace."
            )

        # ====================================================
        # CLEANING LOG
        # ====================================================

        if text != original_text.strip():

            print(
                "[TTS CLEANER] "
                "Formatting cleaned before audio generation."
            )

            print(
                f"[TTS CLEANER] "
                f"Before chars: "
                f"{len(original_text.strip())}"
            )

            print(
                f"[TTS CLEANER] "
                f"After chars : "
                f"{len(text)}"
            )

        else:

            print(
                "[TTS CLEANER] "
                "No formatting changes required."
            )

        output_path = Path(
            output_path
        )

        # ====================================================
        # BUILD PROMPT
        # ====================================================

        prompt = self.build_prompt(
            text
        )

        last_error: Exception | None = None

        # ====================================================
        # RETRIES
        # ====================================================

        for attempt in range(
            1,
            retries + 1,
        ):

            try:

                print()
                print(
                    "=" * 70
                )

                print(
                    "GEMINI ARABIC CHILDREN TTS"
                )

                print(
                    "=" * 70
                )

                print(
                    f"[TTS] Model: "
                    f"{self.model}"
                )

                print(
                    f"[TTS] Voice: "
                    f"{self.voice}"
                )

                print(
                    f"[TTS] Narrator: "
                    f"{self.narrator}"
                )

                print(
                    f"[TTS] Text chars: "
                    f"{len(text)}"
                )

                if self.narrator == "female":

                    print(
                        "[TTS] Style: "
                        "Female UAE Arabic "
                        "Educational Storyteller"
                    )

                else:

                    print(
                        "[TTS] Style: "
                        "Male UAE Arabic "
                        "Storytelling Learning Narrator"
                    )

                print(
                    "[TTS] Audience: "
                    "Children learning Arabic in UAE"
                )

                print(
                    "[TTS] Voice priority: "
                    "Storytelling + Arabic clarity"
                )

                print(
                    "[TTS] Generating audio..."
                )

                # ============================================
                # GEMINI REQUEST
                # ============================================

                interaction = (
                    self.client
                    .interactions
                    .create(
                        model=self.model,

                        input=prompt,

                        response_format={
                            "type": "audio",
                        },

                        generation_config={
                            "speech_config": [
                                {
                                    "voice":
                                        self.voice
                                }
                            ]
                        },
                    )
                )

                # ============================================
                # AUDIO RESPONSE
                # ============================================

                output_audio = getattr(
                    interaction,
                    "output_audio",
                    None,
                )

                if output_audio is None:

                    raise RuntimeError(
                        "Gemini returned no audio."
                    )

                audio_data = getattr(
                    output_audio,
                    "data",
                    None,
                )

                if not audio_data:

                    raise RuntimeError(
                        "Gemini returned empty audio."
                    )

                # ============================================
                # DECODE
                # ============================================

                if isinstance(
                    audio_data,
                    str,
                ):

                    pcm_data = (
                        base64.b64decode(
                            audio_data
                        )
                    )

                else:

                    pcm_data = bytes(
                        audio_data
                    )

                if not pcm_data:

                    raise RuntimeError(
                        "Decoded PCM audio is empty."
                    )

                # ============================================
                # SAVE WAV
                # ============================================

                self.save_wav(
                    output_path,
                    pcm_data,
                )

                print(
                    f"[TTS] Saved: "
                    f"{output_path}"
                )

                print(
                    f"[TTS] PCM bytes: "
                    f"{len(pcm_data)}"
                )

                print(
                    "[TTS] SUCCESS"
                )

                return output_path

            except Exception as exc:

                last_error = exc

                print(
                    f"[TTS ERROR] "
                    f"Attempt "
                    f"{attempt}/{retries}: "
                    f"{exc}"
                )

                if attempt < retries:

                    wait_seconds = min(
                        2 ** attempt,
                        15,
                    )

                    print(
                        f"[TTS RETRY] "
                        f"Waiting "
                        f"{wait_seconds}s..."
                    )

                    time.sleep(
                        wait_seconds
                    )

        raise RuntimeError(
            "Gemini TTS failed after "
            f"{retries} attempts: "
            f"{last_error}"
        )
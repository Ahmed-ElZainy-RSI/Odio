from pathlib import Path

from ocr_verifier.tts.gemini_tts import GeminiTTS


TEXT = """
فَكَّرْتُ فِي كَيْفِيَّةِ مُعَالَجَةِ الْأَمْرِ،
فَأَخَذْتُ أَبْحَثُ عَنْ بَدِيلٍ يُعِيدُ إِلَيْهَا
نَشَاطَهَا وَتَرْكِيزَهَا.
""".strip()


VOICES = [
    "Sulafat",
    "Achernar",
    "Vindemiatrix",
    "Leda",
]


def main():

    output_dir = Path("voice_test")

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("=" * 60)
    print("GEMINI CHILDREN BOOK VOICE TEST")
    print("=" * 60)

    for voice in VOICES:

        print()
        print("-" * 60)
        print(f"VOICE: {voice}")
        print("-" * 60)

        try:

            tts = GeminiTTS(
                voice=voice
            )

            output_path = (
                output_dir
                / f"{voice}.wav"
            )

            tts.generate(
                text=TEXT,
                output_path=output_path,
            )

            print(
                f"[OK] {output_path}"
            )

        except Exception as exc:

            print(
                f"[ERROR] {voice}: {exc}"
            )

    print()
    print("=" * 60)
    print("FINISHED")
    print("=" * 60)
    print()
    print(f"Listen to files in: {output_dir}")


if __name__ == "__main__":
    main()
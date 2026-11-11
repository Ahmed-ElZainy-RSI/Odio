"""Score OCR outputs in <dir>/<page>/ against ground truth.

    python score_ocr.py                              # results/ (prompt without tashkeel request)
    python score_ocr.py --dir results_tashkeel       # runs made with ocr_compare.py --tashkeel
    python score_ocr.py --dir results_tashkeel --csv out.csv

Four numbers per model and page:
  char_acc      letters only: diacritics, punctuation and alef/yaa variants ignored (the original score)
  word_acc      same, on whole words
  tashkeel_acc  of the correctly read letters that carry diacritics on the page OR in the model's
                text, the share whose diacritics match the page exactly. Missing, wrong and added
                marks all count against it: the model must copy the printed tashkeel, not guess.
  added         of the correctly read letters printed without diacritics, the share the model
                vocalized anyway (already counted as errors in tashkeel_acc; shown for diagnosis)
  full_acc      letters and diacritics together as one string, strict: added marks count as errors
"""
import re, sys
from pathlib import Path

# Reference text typed from the printed page, with tashkeel exactly as printed (typos included).
GT = {
 "page_4": "وَظِيفَةُ يَوْمِ العُطْلَةِ كَانَ «عَاصِمُ» وَ«يَاسِرُ» رَفِيقَيْنِ بِصَفٍّ وَاحِدٍ، وَصَدِقَينِ جَيِّدَيْنِ، وَكَانُوا مُتَحَمِّسِينَ جِدًّا لِعُطْلَةِ الصَّيفِ. أَعْطَتْهُمَا مُعَلِّمَةُ الصَّفِّ فروضًا مَنزلِيَّة خِلَالَ العُطْلَةِ، وَقَالَتْ لَهُمَا: «استَمْتِعَا بِالعُطْلَةِ، لَكِنْ لا تَنْسَيَا أَنْ تُنْهِيَا فُرُوضَكُما المَنْزِلِيَّةَ فِي وَقْتِهَا».",
 "page_5": "فِي المَنْزِلِ سَأَلَ «عَاصِمُ» وَالِدَتَهُ: «أُمِّي هَلَّا سَاعِدْتِنِي لَوْ سَمَحْتِ بِالفُرُوضِ المَنْزِلِيَّةِ فى العُطْلَةِ ؟ أُرِيدُ أَنْ أُنْهِيَهَا الآنَ وَأَستَمْتِعَ بِالعُطْلَةِ». وَافَقَتْ وَالِدَتُهُ وَسَاعَدَتْهُ فِي إِنْهَاءِ الفُرُوضِ المَنْزِلِيَّةِ.",
 # «الديناصورات» (ناشيونال جيوجرافيك)، file pages 5, 8, 11 = printed 4, 7, 10. Headings carry no tashkeel.
 "dino_5": "عظام ضخمة ومخيفة! هلْ رأيتَ من قبلُ عظامَ ديناصورٍ في أحدِ المتاحفِ؟ تكونُ بعضُ هذه العظامِ ضخمةً للغايةِ، وإن عادتْ إلى الحياةِ مَرةً أخرَى فستكونُ مخيفةً جدًّا! لكنْ لا تقلقْ، فَقَدِ انقرضتْ كلُّ هذهِ الديناصوراتِ الضخمةِ والمخيفةِ منذُ زمنٍ طويلٍ للغايةِ. إلا أنَّ هذهِ العظامَ لَمْ تَكُنْ موجودةً دائمًا في المتحفِ، فمِنْ أينَ أَتَتْ؟ مصطلحات عَنِ الديناصوراتِ متحفٌ: مبنًى يُمكنُكَ فيه رؤيةُ عظامِ الديناصوراتِ وغيرِها من الأشياءِ المهمةِ والنادرةِ.",
 "dino_8": "التنقيب عن الديناصورات كانتْ عظامُ الديناصوراتِ مدفونةً في مكانٍ آمنٍ داخلَ الصخورِ لوقتٍ طويلٍ للغايةِ. وتُعرَفُ هذهِ العظامُ باسمِ حفرياتٍ، ويَعثُرُ عليها علماءُ الحفرياتِ، فهُم يحفِرونَ الأرَضَ ويستخرجونَها منهَا. مصطلحات عَنِ الديناصوراتِ حفريةٌ: جزءٌ مِن كائنٍ حيٍّ حُفِظَ داخلَ الصخورِ. عالمُ الحفرياتِ: عالمٌ يعثُرُ على الحفرياتِ ويَدْرُسها.",
 # «المومياوات» (ناشيونال جيوجرافيك)، file pages 5, 14 = printed 4, 13. Headings carry no tashkeel.
 "mummy_5": "العثور على مومياء! كان أحدُ المزارعينَ يعملُ فِي أرضِ مُستنقعاتٍ، ثمَّ اصطدمت مجرفتُهُ بشيءٍ صُلبٍ، فأخرج جسدًا أسودَ اللونِ. وُجدَ أنَّ لهذَا الجسدِ شعرًا، وأسنانًا وحتَّى بصماتِ أصابعَ. اتصلَ المزارعُ بالشرطةِ علَى الفورِ. فقد بدا له هذَا الإنسانُ قدْ ماتَ فِي وقتٍ قريبٍ. إلَّا أنَّ هذَا الجثمانَ كانَ لإنسانٍ توفِّيَ منذَ أكثرَ منْ ألفي عامٍ! إنهُ مومياءُ!",
 "mummy_14": "فِي عامِ 1991 عثرَ شخصانِ يتجولانِ علَى الأقدامِ علَى جُثةِ رجلٍ متجمدٍ فِي الجبالِ الموجودةِ بينَ إيطاليا والنمسا. لقد كانتْ جريمةَ قتلٍ غَامضةً حدثتْ منذُ نحو 5300 سنةٍ! إنَّ هذهِ المُومياءَ، التِي عُرفتْ باسم «أوتزي» هيَ واحدةٌ من أقدمِ المومياواتِ التي عُثر عليهَا علَى الإطلاقِ. كانتْ هذهِ المُومياءُ تَرتدِي مِعطفًا، وحذاءً مصنوعًا منَ الجلدِ. إلَّا أنَّ العلماءَ عندمَا فحصُوا جُثةَ أوتزي بعنايةٍ وجدُوا مفاجأةً. لقدْ وجدُوا رأس سهمٍ حجريًّا مغروزًا فِي إحدَى كَتفيهِ. فلقد أُطلقَ سهمٌ علَى أوتزي منَ الخلفِ فقتلَهُ! مَنِ الذِي قتلَ أوتزي منذُ أكثرَ منْ خمسةِ آلافِ سنةٍ؟ ولماذَا؟ حتَّى الآنَ مَا زالتْ هذهِ القضيةُ لمْ يُحلَّ لغزُهَا! شكلٌ تخيُّلِيٌّ لأوتزي",
 "dino_11":"جلد الديناصورات كانَ جلدُ الديناصوراتِ يَترُكُ في بعضِ الأحيانِ أثرًا في الطينِ. يجِفُّ هذا الطينُ ويتصلَّبُ وبالتَّالي يَحْفَظُ شكلَ الجلدِ. نعرِفُ من هذهِ الحفرياتِ أَنَّ بعضَ الديناصوراتِ كانَ له جلدٌ حرشفيٌّ يُشبِهُ جلدَ السَّحَالي. ترايسيراتوبس (ديناصورٌ ثلاثيٌّ القرونِ)",
}

MARKS = "ًٌٍَُِّْٰ"   # tanween, harakat, shadda, sukun, dagger alef
MARK_RE = f"[{MARKS}]"


def norm(t, keep_marks=False):
    t = re.sub(r"<img>.*?</img>", " ", t, flags=re.S)
    t = re.sub(r"^.*\d+\s*\|\s*وظيفة يوم العطلة.*$", " ", t, flags=re.M)  # page footer
    t = t.replace("ـ", "")                                          # tatweel
    t = re.sub(r"ا(ً)", r"\1ا", t)                                  # «اً» and «ًا» are the same
    if not keep_marks:
        t = re.sub(MARK_RE, "", t)
    t = re.sub(r"[أإآٱ]", "ا", t).replace("ى", "ي")
    t = re.sub(rf"[^ء-ي{MARKS}\s]", " ", t)                              # punctuation/latin/markdown
    t = re.sub(rf"(?<![ء-ي{MARKS}]){MARK_RE}+", "", t)                   # marks with no letter
    # one canonical order for the marks on a letter (shadda+fatha == fatha+shadda)
    t = re.sub(rf"{MARK_RE}+", lambda m: "".join(sorted(m.group())), t)
    return " ".join(t.split())


def units(t):
    """'كَانَ' -> [('ك','َ'), ('ا',''), ('ن','َ')]; spaces are units with no marks."""
    return [(m.group(1), m.group(2)) for m in re.finditer(rf"([^{MARKS}])({MARK_RE}*)", t)]


def lev(a, b):
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def aligned_pairs(a, b):
    """Index pairs (i, j) where a[i] == b[j] on a minimal edit path."""
    n, m = len(a), len(b)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): d[i][0] = i
    for j in range(m + 1): d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i-1][j] + 1, d[i][j-1] + 1, d[i-1][j-1] + (a[i-1] != b[j-1]))
    i, j, pairs = n, m, []
    while i and j:
        if a[i-1] == b[j-1] and d[i][j] == d[i-1][j-1]:
            pairs.append((i-1, j-1)); i -= 1; j -= 1
        elif d[i][j] == d[i-1][j-1] + 1: i -= 1; j -= 1
        elif d[i][j] == d[i-1][j] + 1: i -= 1
        else: j -= 1
    return pairs


def score(raw, gt):
    h, g = norm(raw), norm(gt)
    cacc = max(0, 1 - lev(h, g) / len(g))
    wacc = max(0, 1 - lev(h.split(), g.split()) / len(g.split()))
    ht, gtt = norm(raw, True), norm(gt, True)
    facc = max(0, 1 - lev(ht, gtt) / len(gtt))
    hu, gu = units(ht), units(gtt)
    pairs = [(i, j) for i, j in aligned_pairs([u[0] for u in hu], [u[0] for u in gu]) if gu[j][0] != " "]
    marked = [(i, j) for i, j in pairs if gu[j][1]]
    bare = [(i, j) for i, j in pairs if not gu[j][1]]
    either = [(i, j) for i, j in pairs if gu[j][1] or hu[i][1]]
    tacc = sum(hu[i][1] == gu[j][1] for i, j in either) / len(either) if either else 0
    added = sum(bool(hu[i][1]) for i, j in bare) / len(bare) if bare else 0
    return cacc, wacc, tacc, added, facc


def error_kind(raw):
    r = raw.lower()
    if "timed out" in r: return "timeout"
    if "quota" in r or "runs limit" in r: return "quota"
    if "http 429" in r: return "rate limited"
    if "http 402" in r or "credits" in r: return "no credits"
    if "acceleratorerror" in r: return "space error"
    return "other error"


def main():
    import csv
    sys.stdout.reconfigure(encoding="utf-8")
    arg = lambda name, default: sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default
    root = Path(arg("--dir", "results"))
    out = []
    for page, gt in GT.items():
        rows = []
        for f in sorted((root / page).glob("*.txt")):
            raw = f.read_text(encoding="utf-8")
            if not raw.strip():
                rows.append((f.stem, None, None, None, None, None, "empty", "")); continue
            if raw.startswith("[ERROR]"):
                rows.append((f.stem, None, None, None, None, None, error_kind(raw), raw[8:300].strip())); continue
            rows.append((f.stem, *score(raw, gt), "ok", ""))
        if not rows:
            continue
        rows.sort(key=lambda r: (-1, -1) if r[1] is None else (r[3], r[1]), reverse=True)
        print(f"\n{page}\n{'model':<28}{'letters':>9}{'words':>9}{'tashkeel':>10}{'added':>8}{'full':>9}")
        for m, c, w, t, ad, fa, st, _ in rows:
            print(f"{m:<28}" + (f"{st:>20}" if c is None else f"{c:>8.1%}{w:>9.1%}{t:>10.1%}{ad:>8.1%}{fa:>9.1%}"))
        out += [(page, *r) for r in rows]
    if "--csv" in sys.argv:
        path = arg("--csv", None)
        r4 = lambda v: "" if v is None else round(v, 4)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["page", "model", "char_acc", "word_acc", "tashkeel_acc", "added", "full_acc", "status", "error"])
            for page, m, c, wa, t, ad, fa, st, err in out:
                w.writerow([page, m, r4(c), r4(wa), r4(t), r4(ad), r4(fa), st, err])
        print(f"\nWrote {path}")


if __name__ == "__main__":
    main()

"""
Offline AI fallback engine for generating quizzes, summaries, flashcards,
and study tools heuristically when no live AI API is configured.
"""

from collections import Counter
import random
import re
from typing import Dict, List, Tuple

from bot.config import MAX_QUESTION_LENGTH
from bot.constants import (
    AI_STOPWORDS_AR,
    AI_STOPWORDS_EN,
    LOCAL_GENERIC_DISTRACTORS,
)
from bot.i18n.texts import get_text
from bot.utils.helpers import normalize_ai_tool, normalize_fun_style


def local_fun_break_message(style: str, lang: str = "ar") -> str:
    """Generate a light fun break or trivia message for groups."""
    style = normalize_fun_style(style)
    pick = random.choice
    banks = {
        "trivia": {
            "en": [
                "Quick trivia: Honey never spoils under the right conditions.",
                "Quick trivia: Octopuses have three hearts.",
                "Quick trivia: A day on Venus is longer than a year on Venus.",
            ],
            "ar": [
                "معلومة سريعة: العسل لا يفسد بسهولة في الظروف المناسبة.",
                "معلومة سريعة: للأخطبوط ثلاثة قلوب.",
                "معلومة سريعة: اليوم في كوكب الزهرة أطول من سنته.",
            ],
        },
        "joke": {
            "en": [
                "Tiny joke break: Why did the student bring a ladder? To reach the high marks.",
                "Tiny joke break: I would tell you a chemistry joke, but I know I would get no reaction.",
            ],
            "ar": [
                "فاصل لطيف: لماذا أحضر الطالب سلماً؟ ليصل إلى أعلى الدرجات.",
                "فاصل لطيف: كنت سأقول نكتة كيمياء، لكن يبدو أن التفاعل لن يكون قوياً.",
            ],
        },
        "riddle": {
            "en": [
                "Riddle break: I speak without a mouth and hear without ears. What am I? Echo.",
                "Riddle break: The more you take, the more you leave behind. What is it? Footsteps.",
            ],
            "ar": [
                "فاصل لغز: أتكلم بلا فم وأسمع بلا أذن. ما أنا؟ الصدى.",
                "فاصل لغز: كلما أخذت مني أكثر، تركت خلفك أكثر. ما أنا؟ الآثار.",
            ],
        },
        "icebreaker": {
            "en": [
                "Icebreaker: If this group could learn one new skill together this month, what should it be?",
                "Icebreaker: Coffee, tea, or something else for your best study session?",
            ],
            "ar": [
                "كسر الجليد: لو كان على هذه المجموعة تعلم مهارة واحدة هذا الشهر، فما هي؟",
                "كسر الجليد: قهوة أم شاي أم شيء آخر لأفضل جلسة مذاكرة؟",
            ],
        },
        "poll": {
            "en": [
                "Quick poll: What breaks your study streak most often? A) Notifications B) Fatigue C) Noise D) Phone",
                "Quick poll: Which format helps you most? A) MCQ B) Summary C) Flashcards D) Mixed",
            ],
            "ar": [
                "تصويت سريع: ما أكثر شيء يقطع تركيزك؟ أ) الإشعارات ب) التعب ج) الضوضاء د) الهاتف",
                "تصويت سريع: أي صيغة تساعدك أكثر؟ أ) MCQ ب) ملخص ج) بطاقات د) خليط",
            ],
        },
        "study": {
            "en": [
                "Study break: What is the one concept here you would explain to a friend in 10 seconds?",
                "Study break: Write one flashcard question from this lesson before continuing.",
                "Study break: What is the main takeaway from the last question?",
            ],
            "ar": [
                "فاصل دراسي: ما الفكرة الواحدة هنا التي يمكنك شرحها لصديق في 10 ثوانٍ؟",
                "فاصل دراسي: اكتب سؤال بطاقة واحدة من هذا الدرس قبل المتابعة.",
                "فاصل دراسي: ما الخلاصة الأساسية من السؤال الأخير؟",
            ],
        },
    }
    if style == "mixed":
        style = random.choice(["trivia", "joke", "riddle", "icebreaker", "poll", "study"])
    options = banks.get(style, banks["trivia"]).get(lang, banks["trivia"]["en"])
    return pick(options)


def split_local_units(text: str) -> List[str]:
    """Split text into sentences or clean meaningful units."""
    clean = " ".join((text or "").split())
    if not clean:
        return []
    lines = [line.strip("-• \t") for line in (text or "").splitlines() if line.strip()]
    if len(lines) >= 2:
        return lines
    sentences = [s.strip() for s in re.split(r"(?<=[.!?؟])\s+", clean) if s.strip()]
    return sentences if sentences else [clean]


def _normalize_for_compare(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip().lower())


def extract_key_terms(text: str, limit: int = 8) -> List[str]:
    """Extract key keywords and phrases using frequency and heuristics."""
    raw_tokens = re.findall(r"[A-Za-z][A-Za-z0-9'-]{2,}|[\u0600-\u06FF]{2,}|\d+(?:\.\d+)?", text or "")
    tokens: List[Tuple[int, str, str]] = []
    for index, raw in enumerate(raw_tokens):
        token = raw.strip("'-_")
        if not token:
            continue
        norm = token.lower()
        if norm in AI_STOPWORDS_EN or norm in AI_STOPWORDS_AR:
            continue
        if len(norm) < 3 and not re.fullmatch(r"\d+(?:\.\d+)?", norm):
            continue
        tokens.append((index, token, norm))

    if not tokens:
        return []

    scores: Counter[str] = Counter()
    first_seen: Dict[str, int] = {}
    display: Dict[str, str] = {}

    for index, token, norm in tokens:
        if norm not in display:
            display[norm] = token
            first_seen[norm] = index
        weight = 1.0
        if token[:1].isupper() and token[1:].islower():
            weight += 0.2
        if re.fullmatch(r"\d+(?:\.\d+)?", token):
            weight += 0.35
        if len(token) >= 8:
            weight += 0.1
        scores[norm] += weight

    for size, bonus in ((2, 0.8), (3, 1.1)):
        for start in range(0, len(tokens) - size + 1):
            window = tokens[start:start + size]
            if any(item[2] in AI_STOPWORDS_EN or item[2] in AI_STOPWORDS_AR for item in window):
                continue
            phrase = " ".join(item[1] for item in window)
            norm_phrase = _normalize_for_compare(phrase)
            if len(norm_phrase) < 5:
                continue
            scores[norm_phrase] += bonus + (0.15 * size)
            display.setdefault(norm_phrase, phrase)
            first_seen.setdefault(norm_phrase, window[0][0])

    ordered = sorted(scores.keys(), key=lambda key: (-scores[key], first_seen.get(key, 10**9), len(display.get(key, key))))
    result: List[str] = []
    seen_norms: set[str] = set()
    for key in ordered:
        candidate = display.get(key, key)
        norm = _normalize_for_compare(candidate)
        if not norm or norm in seen_norms:
            continue
        if any(norm == _normalize_for_compare(existing) for existing in result):
            continue
        result.append(candidate)
        seen_norms.add(norm)
        if len(result) >= max(1, limit):
            break
    return result


def _build_local_options(answer: str, pool: List[str], lang: str) -> Tuple[List[str], int]:
    """Generate 4 plausible options for offline quizzes."""
    normalized_answer = _normalize_for_compare(answer)
    options = [answer]
    for candidate in pool:
        if len(options) >= 4:
            break
        candidate_norm = _normalize_for_compare(candidate)
        if candidate_norm and candidate_norm != normalized_answer and candidate_norm not in {_normalize_for_compare(opt) for opt in options}:
            options.append(candidate)
    for generic in LOCAL_GENERIC_DISTRACTORS.get(lang, LOCAL_GENERIC_DISTRACTORS["en"]):
        if len(options) >= 4:
            break
        generic_norm = _normalize_for_compare(generic)
        if generic_norm not in {_normalize_for_compare(opt) for opt in options}:
            options.append(generic)
    while len(options) < 4:
        filler = f"Option {len(options) + 1}" if lang == "en" else f"خيار {len(options) + 1}"
        if _normalize_for_compare(filler) not in {_normalize_for_compare(opt) for opt in options}:
            options.append(filler)
    random.shuffle(options)
    return options, options.index(answer)


def _mask_term_in_text(text: str, term: str) -> str:
    if not text or not term:
        return text
    pattern = re.compile(re.escape(term), re.I)
    if pattern.search(text):
        return pattern.sub("____", text, count=1)
    return text


def local_quiz_pack(payload: str, lang: str, count: int, mode: str) -> List[Tuple[str, List[str], int, str]]:
    """Generate offline quiz questions from text or topic."""
    text = " ".join((payload or "").split())
    if not text:
        return []
    count = max(1, min(10, count))
    units = split_local_units(text)
    terms = extract_key_terms(text, max(10, count * 4))
    if not terms:
        terms = [units[0][:32] if units else text[:32]]
    source_hint = (units[0] if units else text)[:120]
    quizzes: List[Tuple[str, List[str], int, str]] = []
    for index in range(count):
        answer = terms[index % len(terms)]
        distractor_pool = [term for term in terms if _normalize_for_compare(term) != _normalize_for_compare(answer)]
        distractor_pool.extend(unit[:48] for unit in units if _normalize_for_compare(unit) != _normalize_for_compare(answer))
        options, correct_index = _build_local_options(answer, distractor_pool, lang)
        snippet = units[index % len(units)] if units else text
        style = index % 4
        if mode == "topic":
            if style == 0:
                question = f"ما المفهوم الأكثر ارتباطاً بهذا الموضوع: {text[:85]}؟" if lang == "ar" else f"Which concept is most closely tied to this topic: {text[:85]}?"
            elif style == 1:
                question = f"ما المصطلح الأفضل لتمثيل هذا الموضوع: {text[:85]}؟" if lang == "ar" else f"Which term best represents this topic: {text[:85]}?"
            elif style == 2:
                question = f"أي عبارة تلخص هذا الموضوع أفضل؟ {text[:85]}" if lang == "ar" else f"Which statement best summarizes this topic: {text[:85]}?"
            else:
                question = f"أي كلمة مفتاحية تندرج تحت هذا الموضوع؟ {text[:85]}" if lang == "ar" else f"Which key term fits this topic best: {text[:85]}?"
            explanation = f"هذا السؤال مبني على الموضوع: {text[:120]}" if lang == "ar" else f"This question is based on the topic: {text[:120]}"
        else:
            if style == 0:
                masked = _mask_term_in_text(snippet, answer)
                if masked == snippet:
                    masked = _mask_term_in_text(source_hint, answer)
                question = (
                    f"ما الكلمة المناسبة لإكمال العبارة التالية: {masked[:100]}؟"
                    if lang == "ar"
                    else f"Which word best completes this line: {masked[:100]}?"
                )
            elif style == 1:
                question = (
                    f"ما المصطلح الأكثر ارتباطاً بهذه الفقرة: {snippet[:95]}؟"
                    if lang == "ar"
                    else f"Which term is most relevant to this excerpt: {snippet[:95]}?"
                )
            elif style == 2:
                question = (
                    f"ما الفكرة الأساسية التي يعبر عنها هذا النص: {snippet[:95]}؟"
                    if lang == "ar"
                    else f"What main idea does this text express: {snippet[:95]}?"
                )
            else:
                question = (
                    f"أي خيار يطابق هذه الفقرة بشكل أفضل: {snippet[:95]}؟"
                    if lang == "ar"
                    else f"Which option best matches this excerpt: {snippet[:95]}?"
                )
            explanation = snippet[:160] or source_hint
        quizzes.append((question[:MAX_QUESTION_LENGTH], options, correct_index, explanation[:700]))
    return quizzes


def local_study_pack(payload: str, lang: str) -> str:
    """Generate offline study pack containing summary, key points, terms, and flashcards."""
    text = " ".join((payload or "").split())
    if not text:
        return get_text("study_help", lang)

    sentences = [s.strip() for s in re.split(r"(?<=[.!?؟])\s+", text) if s.strip()]
    lines = [line.strip("-• \t") for line in text.splitlines() if line.strip()]
    core = lines[:4] if len(lines) >= 2 else sentences[:4]
    if not core:
        core = [text[:140]]
    key_terms = extract_key_terms(text, 5)
    summary_items = core[:3]
    key_points = core[:4]
    topic_hint = summary_items[0][:90] if summary_items else text[:90]

    if lang == "ar":
        terms_block = "\n".join(f"- {term}" for term in key_terms) if key_terms else "- مصطلحات غير واضحة"
        return (
            "حزمة مذاكرة سريعة:\n\n"
            "الملخص:\n"
            + "\n".join(f"- {item}" for item in summary_items)
            + "\n\nالنقاط المهمة:\n"
            + "\n".join(f"- {item}" for item in key_points)
            + "\n\nالمصطلحات الرئيسية:\n"
            + terms_block
            + "\n\nبطاقات:\n"
            + f"- س: ما الفكرة الأساسية؟\n- ج: {topic_hint}\n"
            + f"- س: اذكر مصطلحاً أساسياً من الدرس.\n- ج: {key_terms[0] if key_terms else topic_hint}\n\n"
            + "اختبار سريع:\n"
            + f"- س: ما الذي يجب تذكره من هذا النص؟\n- ج: {topic_hint}"
        )

    terms_block = "\n".join(f"- {term}" for term in key_terms) if key_terms else "- No clear terms extracted"
    return (
        "Study pack:\n\n"
        "Summary:\n"
        + "\n".join(f"- {item}" for item in summary_items)
        + "\n\nKey points:\n"
        + "\n".join(f"- {item}" for item in key_points)
        + "\n\nKey terms:\n"
        + terms_block
        + "\n\nFlashcards:\n"
        + f"- Q: What is the main idea?\n- A: {topic_hint}\n"
        + f"- Q: Name one key term.\n- A: {key_terms[0] if key_terms else topic_hint}\n\n"
        + "Quick quiz:\n"
        + f"- Q: What should you remember from this text?\n- A: {topic_hint}"
    )


def local_summary_text(payload: str, lang: str) -> str:
    text = " ".join((payload or "").split())
    units = split_local_units(text)
    if not units:
        return get_text("usage_study", lang)
    summary_items = units[:4]
    key_terms = extract_key_terms(text, 4)
    if lang == "ar":
        terms_block = "\n".join(f"- {term}" for term in key_terms) if key_terms else "- لا توجد مصطلحات واضحة"
        return "ملخص سريع:\n" + "\n".join(f"- {item}" for item in summary_items) + "\n\nمصطلحات رئيسية:\n" + terms_block
    terms_block = "\n".join(f"- {term}" for term in key_terms) if key_terms else "- No clear terms extracted"
    return "Quick summary:\n" + "\n".join(f"- {item}" for item in summary_items) + "\n\nKey terms:\n" + terms_block


def local_flashcards_text(payload: str, lang: str) -> str:
    text = " ".join((payload or "").split())
    terms = extract_key_terms(text, 4)
    if not terms:
        return local_study_pack(payload, lang)
    if lang == "ar":
        lines = ["بطاقات سريعة:"]
        for term in terms[:4]:
            lines.append(f"- س: ما علاقة {term} بالموضوع؟")
            lines.append(f"- ج: {term}")
        return "\n".join(lines)
    lines = ["Flashcards:"]
    for term in terms[:4]:
        lines.append(f"- Q: What is {term}?")
        lines.append(f"- A: {term}")
    return "\n".join(lines)


def local_glossary_text(payload: str, lang: str) -> str:
    text = " ".join((payload or "").split())
    terms = extract_key_terms(text, 6)
    if not terms:
        return local_study_pack(payload, lang)
    if lang == "ar":
        lines = ["قاموس مصطلحات:"]
        for term in terms:
            lines.append(f"- {term}: تعريف مختصر من السياق.")
        return "\n".join(lines)
    lines = ["Glossary:"]
    for term in terms:
        lines.append(f"- {term}: brief context-based definition.")
    return "\n".join(lines)


def local_factcheck_text(payload: str, lang: str) -> str:
    units = split_local_units(payload)
    if lang == "ar":
        return (
            "تحقق سريع:\n"
            + "\n".join(f"- مدعوم من النص: {item}" for item in units[:2])
            + "\n- نقاط تحتاج مراجعة: أي ادعاءات لا تظهر بوضوح في النص."
        )
    return (
        "Quick fact check:\n"
        + "\n".join(f"- Supported by the text: {item}" for item in units[:2])
        + "\n- Needs review: any claim not stated clearly in the source."
    )


def local_truefalse_text(payload: str, lang: str) -> str:
    terms = extract_key_terms(payload, 3)
    if not terms:
        return local_study_pack(payload, lang)
    if lang == "ar":
        return (
            "صح / خطأ:\n"
            + "\n".join(
                [
                    f"- 1) {terms[0]} من الأفكار الأساسية في النص. - الإجابة: صح",
                    f"- 2) {terms[0]} لا علاقة له بالنص. - الإجابة: خطأ",
                    f"- 3) {terms[-1]} يظهر كفكرة مهمة. - الإجابة: صح",
                ]
            )
        )
    return (
        "True / False practice:\n"
        + "\n".join(
            [
                f"- 1) {terms[0]} is a key idea in the text. - Answer: True",
                f"- 2) {terms[0]} is unrelated to the source. - Answer: False",
                f"- 3) {terms[-1]} appears as an important idea. - Answer: True",
            ]
        )
    )


def local_shortanswer_text(payload: str, lang: str) -> str:
    terms = extract_key_terms(payload, 3)
    if not terms:
        return local_study_pack(payload, lang)
    if lang == "ar":
        return (
            "إجابات قصيرة:\n"
            + "\n".join(f"- س: ما دور {term} في النص؟\n  ج: {term}" for term in terms[:3])
        )
    return (
        "Short-answer practice:\n"
        + "\n".join(f"- Q: What is the role of {term} in the text?\n  A: {term}" for term in terms[:3])
    )


def local_mnemonic_text(payload: str, lang: str) -> str:
    terms = extract_key_terms(payload, 5)
    if not terms:
        return local_study_pack(payload, lang)
    initials = "".join(term[0].upper() for term in terms if term)
    if lang == "ar":
        return f"وسيلة حفظ: {initials}\n- اربط الحروف الأولى بكلماتك الأساسية: {', '.join(terms)}"
    return f"Mnemonic: {initials}\n- Link the initials to your key terms: {', '.join(terms)}"


def local_analogy_text(payload: str, lang: str) -> str:
    terms = extract_key_terms(payload, 2)
    topic = terms[0] if terms else (split_local_units(payload)[0][:40] if payload else "")
    if lang == "ar":
        return f"تشبيه: فكر في {topic} كأنه قطعة في صورة كبيرة، كل جزء يوضح المعنى بشكل أفضل."
    return f"Analogy: Think of {topic} like one piece in a larger puzzle, where each part makes the picture clearer."


def local_simplify_text(payload: str, lang: str) -> str:
    items = split_local_units(payload)
    if not items:
        return local_study_pack(payload, lang)
    if lang == "ar":
        return "تبسيط:\n" + "\n".join(f"- {item[:120]}" for item in items[:4])
    return "Simplified version:\n" + "\n".join(f"- {item[:120]}" for item in items[:4])


def local_poll_text(payload: str, lang: str) -> str:
    terms = extract_key_terms(payload, 1)
    topic = terms[0] if terms else (split_local_units(payload)[0][:40] if payload else "")
    if lang == "ar":
        return (
            f"تصويت سريع حول {topic}:\n"
            "- أ) أحتاج ملخصاً\n"
            "- ب) أحتاج بطاقات\n"
            "- ج) أحتاج اختباراً سريعاً\n"
            "- د) أحتاج شرحاً أبسط"
        )
    return (
        f"Quick poll about {topic}:\n"
        "- A) I need a summary\n"
        "- B) I need flashcards\n"
        "- C) I need a quick quiz\n"
        "- D) I need a simpler explanation"
    )


def local_challenge_text(payload: str, lang: str) -> str:
    terms = extract_key_terms(payload, 4)
    topic = terms[0] if terms else (split_local_units(payload)[0][:40] if payload else "")
    if lang == "ar":
        return (
            f"تحدي متقدم حول {topic}:\n"
            "- كيف تربط بين الفكرة الأساسية والتفاصيل الفرعية؟\n"
            "- ما السؤال الذي يمكن أن يربك الطالب هنا؟\n"
            "- كيف تشرح هذا الدرس في 30 ثانية؟"
        )
    return (
        f"Challenge on {topic}:\n"
        "- How do you connect the main idea to the supporting details?\n"
        "- What question would most likely trip up a learner here?\n"
        "- How would you explain this lesson in 30 seconds?"
    )


def local_fillblank_text(payload: str, lang: str) -> str:
    items = split_local_units(payload)
    terms = extract_key_terms(payload, 1)
    if not items or not terms:
        return local_study_pack(payload, lang)
    sentence = items[0]
    term = terms[0]
    masked = _mask_term_in_text(sentence, term)
    if lang == "ar":
        return f"أكمل الفراغ:\n- {masked}\n- الإجابة: {term}"
    return f"Fill in the blank:\n- {masked}\n- Answer: {term}"


def local_translate_note(lang: str) -> str:
    if lang == "ar":
        return (
            "الترجمة الدقيقة تحتاج مزوداً فعلياً للذكاء الاصطناعي.\n"
            "إذا أردت الترجمة المجانية، فعّل Ollama محلياً أو اختر مزوداً متوافقاً من /providers."
        )
    return (
        "Accurate translation needs a live AI provider.\n"
        "For a free setup, run Ollama locally or pick a compatible provider from /providers."
    )


def local_ai_tool_text(tool: str, payload: str, lang: str) -> str:
    selected = normalize_ai_tool(tool)
    if selected in {"summary", "keypoints", "recap", "studypack"}:
        if selected in {"summary", "keypoints"}:
            return local_summary_text(payload, lang)
        return local_study_pack(payload, lang)
    if selected == "flashcards":
        return local_flashcards_text(payload, lang)
    if selected == "truefalse":
        return local_truefalse_text(payload, lang)
    if selected == "shortanswer":
        return local_shortanswer_text(payload, lang)
    if selected == "glossary":
        return local_glossary_text(payload, lang)
    if selected == "mnemonic":
        return local_mnemonic_text(payload, lang)
    if selected == "analogy":
        return local_analogy_text(payload, lang)
    if selected == "simplify":
        return local_simplify_text(payload, lang)
    if selected == "challenge":
        return local_challenge_text(payload, lang)
    if selected == "poll":
        return local_poll_text(payload, lang)
    if selected == "factcheck":
        return local_factcheck_text(payload, lang)
    if selected == "fillblank":
        return local_fillblank_text(payload, lang)
    if selected == "translate":
        return local_translate_note(lang)
    if selected == "explain":
        return local_summary_text(payload, lang)
    if selected in {"trivia", "joke", "riddle", "icebreaker"}:
        return local_fun_break_message(selected, lang)
    return local_study_pack(payload, lang)

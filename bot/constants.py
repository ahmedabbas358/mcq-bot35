"""
Constants, regex patterns, AI provider profiles, tool catalogs, and stopwords.
"""

import re
from typing import Dict, List, Set
from bot.config import OPENAI_MODEL, GEMINI_MODEL

# Digit and letter normalization mappings
ARABIC_DIGITS: Dict[str, str] = {
    **{str(i): str(i) for i in range(10)},
    **{"٠": "0", "١": "1", "٢": "2", "٣": "3", "٤": "4", "٥": "5", "٦": "6", "٧": "7", "٨": "8", "٩": "9"},
}

ARABIC_LETTERS: Dict[str, str] = {
    "أ": "A",
    "ا": "A",
    "إ": "A",
    "آ": "A",
    "ب": "B",
    "ت": "C",
    "ث": "D",
    "ج": "E",
    "ح": "F",
    "خ": "G",
    "د": "H",
    "ذ": "I",
    "ر": "J",
    "ز": "K",
    "س": "L",
    "ش": "M",
    "ص": "N",
    "ض": "O",
    "ط": "P",
    "ظ": "Q",
    "ع": "R",
    "غ": "S",
    "ف": "T",
    "ق": "U",
    "ك": "V",
    "ل": "W",
    "م": "X",
    "ن": "Y",
    "ه": "Z",
    "ة": "Z",
    "و": "W",
    "ي": "Y",
    "ئ": "Y",
    "ء": "",
}

QUESTION_PREFIXES = [
    "Q", "Question", "س", "سؤال", "MCQ", "Multiple Choice", "اختبار", "اختر", "أسئلة", "Questions", "السؤال"
]

ANSWER_KEYWORDS = [
    "Answer", "Ans", "Correct Answer", "الإجابة", "الجواب", "الإجابة الصحيحة",
    "Correct", "Solution", "Key", "مفتاح", "صحيح", "صح", "الحل"
]

# Patterns for detecting MCQ options
MCQ_OPTION_PATTERNS = [
    r"^\s*([a-zأ-ي0-9\u0660-\u0669\u06f0-\u06f9])\s*[).:\-]\s*(.+)",
    r"^\s*[\(\[]\s*([a-zأ-ي0-9])\s*[\)\]]\s*(.+)",
    r"^\s*[\u25cb\u25cf\u25a0\u2022\u00d8\*]\s*([a-zأ-ي0-9])\s*[:.]?\s*(.+)",
    r"^\s*([a-zأ-ي0-9])\s*[\u2013\u2014]\s*(.+)",
    r"^\s*\b(?:option|اختيار)\s*([a-zأ-ي0-9])\s*[:.]\s*(.+)",
    r"^\s*([①②③④⑤⑥⑦⑧⑨⑩])\s*[:.]?\s*(.+)",
]

MCQ_UNLABELED_OPTION_PATTERN = r"^\s*[-*•]\s+(.+)"

MCQ_BLOCK_START_RE = re.compile(
    r"^\s*(?:(?:Q(?:uestion)?|MCQ|س(?:ؤال)?)\s*[\d\u0660-\u0669\u06f0-\u06f9]*\s*[\).:\-]?"
    r"|[\[(]?\s*[\d\u0660-\u0669\u06f0-\u06f9]+\s*[\])\.:\-])\s*",
    re.I,
)

MCQ_EXPLANATION_RE = re.compile(
    r"^\s*(?:Explanation|Exp|Reason|Note|Reference|Source|شرح|الشرح|تفسير|التفسير|توضيح|التوضيح|ملاحظة|مرجع)\s*[:\-]",
    re.I,
)

MCQ_REFERENCE_ONLY_RE = re.compile(r"^\s*[\[(]\s*[\d\u0660-\u0669\u06f0-\u06f9]{1,4}\s*[\])]\s*$", re.I)

# AI Tools & Modes
AI_TOOL_CATALOG = {
    "quiz": {"en": "Quiz generator", "ar": "مولد اختبارات", "desc_en": "Turn text or a topic into MCQs.", "desc_ar": "حوّل النص أو الموضوع إلى أسئلة اختيار من متعدد."},
    "explain": {"en": "Explain", "ar": "شرح", "desc_en": "Explain the topic in a clear way.", "desc_ar": "اشرح الموضوع بطريقة واضحة."},
    "summary": {"en": "Summary", "ar": "تلخيص", "desc_en": "Summarize long text into short points.", "desc_ar": "لخص النص الطويل في نقاط قصيرة."},
    "translate": {"en": "Translate", "ar": "ترجمة", "desc_en": "Translate between Arabic and English.", "desc_ar": "ترجم بين العربية والإنجليزية."},
    "flashcards": {"en": "Flashcards", "ar": "بطاقات", "desc_en": "Create study flashcards.", "desc_ar": "أنشئ بطاقات للمذاكرة."},
    "truefalse": {"en": "True / False", "ar": "صح / خطأ", "desc_en": "Create true/false statements.", "desc_ar": "أنشئ عبارات صح أو خطأ."},
    "shortanswer": {"en": "Short answers", "ar": "إجابات قصيرة", "desc_en": "Generate short-answer questions.", "desc_ar": "أنشئ أسئلة بإجابات قصيرة."},
    "keypoints": {"en": "Key points", "ar": "النقاط المهمة", "desc_en": "Extract the most important points.", "desc_ar": "استخرج أهم النقاط."},
    "glossary": {"en": "Glossary", "ar": "قاموس مصطلحات", "desc_en": "List key terms and definitions.", "desc_ar": "سرد المصطلحات المهمة وتعريفاتها."},
    "mnemonic": {"en": "Mnemonic", "ar": "وسيلة حفظ", "desc_en": "Create memory aids.", "desc_ar": "ابتكر وسيلة تساعد على الحفظ."},
    "analogy": {"en": "Analogy", "ar": "تشبيه", "desc_en": "Explain using a relatable analogy.", "desc_ar": "اشرح باستخدام تشبيه قريب."},
    "simplify": {"en": "Simplify", "ar": "تبسيط", "desc_en": "Rewrite complex text in simpler language.", "desc_ar": "أعد صياغة النص المعقد بلغة أبسط."},
    "challenge": {"en": "Challenge", "ar": "تحدي", "desc_en": "Create harder challenge questions.", "desc_ar": "أنشئ أسئلة أصعب للتحدي."},
    "poll": {"en": "Poll idea", "ar": "فكرة تصويت", "desc_en": "Create a quick poll or opinion check.", "desc_ar": "أنشئ تصويتاً سريعاً أو سؤال رأي."},
    "recap": {"en": "Recap sheet", "ar": "مراجعة سريعة", "desc_en": "Make a compact revision sheet.", "desc_ar": "أنشئ ورقة مراجعة مختصرة."},
    "factcheck": {"en": "Fact check", "ar": "تحقق", "desc_en": "Check claims and flag weak spots.", "desc_ar": "تحقق من الادعاءات ونقاط الضعف."},
    "fillblank": {"en": "Fill in blanks", "ar": "أكمل الفراغ", "desc_en": "Create cloze-style questions.", "desc_ar": "أنشئ أسئلة إكمال فراغات."},
    "studypack": {"en": "Study pack", "ar": "حزمة مذاكرة", "desc_en": "Create a summary, key points, flashcards, and a quick quiz.", "desc_ar": "أنشئ ملخصاً ونقاطاً مهمة وبطاقات واختباراً سريعاً."},
    "riddle": {"en": "Riddle", "ar": "لغز", "desc_en": "Create a short riddle.", "desc_ar": "أنشئ لغزاً قصيراً."},
    "joke": {"en": "Joke", "ar": "نكتة", "desc_en": "Create a light joke or playful line.", "desc_ar": "أنشئ نكتة خفيفة أو عبارة لطيفة."},
    "icebreaker": {"en": "Icebreaker", "ar": "كسر الجليد", "desc_en": "Create a quick group conversation prompt.", "desc_ar": "أنشئ سؤالاً سريعاً للمجموعة."},
}

AI_TOOL_ALIASES = {
    "mcq": "quiz",
    "multiplechoice": "quiz",
    "multiple-choice": "quiz",
    "qa": "shortanswer",
    "question": "quiz",
    "questions": "quiz",
    "truefalse": "truefalse",
    "true_false": "truefalse",
    "keypoints": "keypoints",
    "keypointsheet": "recap",
    "recap": "recap",
    "facts": "factcheck",
    "check": "factcheck",
    "cloze": "fillblank",
    "blank": "fillblank",
    "study": "studypack",
    "studypack": "studypack",
    "revise": "studypack",
    "learn": "studypack",
}

FUN_STYLE_CHOICES = {"mixed", "trivia", "joke", "riddle", "icebreaker", "poll", "study"}

AI_TOOL_SPECS = {
    "quiz": {"instruction": "Create exactly 4-option MCQ quizzes in JSON only.", "temperature": 0.2, "max_tokens": 2200},
    "explain": {"instruction": "Explain the topic clearly and step by step.", "temperature": 0.2, "max_tokens": 1000},
    "summary": {"instruction": "Summarize the input into concise bullet points.", "temperature": 0.2, "max_tokens": 900},
    "translate": {"instruction": "Translate the input accurately without adding extra commentary.", "temperature": 0.1, "max_tokens": 900},
    "flashcards": {"instruction": "Turn the input into study flashcards with term and meaning.", "temperature": 0.2, "max_tokens": 1100},
    "truefalse": {"instruction": "Create a short true/false practice set.", "temperature": 0.2, "max_tokens": 900},
    "shortanswer": {"instruction": "Create short-answer questions and model answers.", "temperature": 0.2, "max_tokens": 1000},
    "keypoints": {"instruction": "Extract the most important points only.", "temperature": 0.2, "max_tokens": 800},
    "glossary": {"instruction": "List the key terms and simple definitions.", "temperature": 0.2, "max_tokens": 1000},
    "mnemonic": {"instruction": "Create easy mnemonics or memory aids.", "temperature": 0.5, "max_tokens": 800},
    "analogy": {"instruction": "Explain the idea using a clear real-world analogy.", "temperature": 0.3, "max_tokens": 900},
    "simplify": {"instruction": "Rewrite the content in simpler language.", "temperature": 0.2, "max_tokens": 1000},
    "challenge": {"instruction": "Create harder challenge questions that test deep understanding.", "temperature": 0.3, "max_tokens": 1200},
    "poll": {"instruction": "Create one short poll idea with 3 to 4 options.", "temperature": 0.4, "max_tokens": 700},
    "recap": {"instruction": "Create a compact revision sheet with headings and bullets.", "temperature": 0.2, "max_tokens": 1000},
    "factcheck": {"instruction": "Evaluate statements, point out mistakes, and explain the correct facts.", "temperature": 0.2, "max_tokens": 1000},
    "fillblank": {"instruction": "Create fill-in-the-blank sentences and list the missing words.", "temperature": 0.2, "max_tokens": 900},
    "studypack": {"instruction": "Create a complete study pack with summary, key points, flashcards, and a 3-question quiz.", "temperature": 0.2, "max_tokens": 2200},
    "riddle": {"instruction": "Create one clever educational riddle with the answer at the end.", "temperature": 0.6, "max_tokens": 600},
    "joke": {"instruction": "Create a light, clean, study-related joke.", "temperature": 0.6, "max_tokens": 500},
    "icebreaker": {"instruction": "Create an engaging group icebreaker question related to study or general knowledge.", "temperature": 0.5, "max_tokens": 500},
}

# AI Providers Catalog
AI_PROVIDER_CATALOG = {
    "auto": {
        "en": "Auto / current env",
        "ar": "تلقائي / بيئة التشغيل",
        "base_url": "",
        "api_key_env": "",
        "default_model": OPENAI_MODEL,
        "mode": "compatible",
    },
    "openai": {
        "en": "OpenAI",
        "ar": "أوبن إيه آي",
        "base_url": "https://api.openai.com/v1",
        "api_key_env": "OPENAI_API_KEY",
        "default_model": OPENAI_MODEL,
        "mode": "compatible",
    },
    "ollama": {
        "en": "Ollama local",
        "ar": "Ollama المحلي",
        "base_url": "http://localhost:11434/v1",
        "api_key_env": "OLLAMA_API_KEY",
        "default_model": "qwen2.5:14b-instruct",
        "mode": "compatible",
    },
    "gemini": {
        "en": "Gemini API",
        "ar": "Gemini API",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "default_model": GEMINI_MODEL,
        "mode": "compatible",
    },
    "openrouter": {
        "en": "OpenRouter",
        "ar": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "default_model": "openai/gpt-4o-mini",
        "mode": "compatible",
    },
    "groq": {
        "en": "Groq",
        "ar": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "default_model": "llama-3.3-70b-versatile",
        "mode": "compatible",
    },
    "together": {
        "en": "Together AI",
        "ar": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "api_key_env": "TOGETHER_API_KEY",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
        "mode": "compatible",
    },
    "fireworks": {
        "en": "Fireworks",
        "ar": "Fireworks",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "api_key_env": "FIREWORKS_API_KEY",
        "default_model": "accounts/fireworks/models/llama-v3p3-70b-instruct",
        "mode": "compatible",
    },
    "deepinfra": {
        "en": "DeepInfra",
        "ar": "DeepInfra",
        "base_url": "https://api.deepinfra.com/v1/openai",
        "api_key_env": "DEEPINFRA_API_KEY",
        "default_model": "meta-llama/Llama-3.3-70B-Instruct",
        "mode": "compatible",
    },
    "perplexity": {
        "en": "Perplexity",
        "ar": "Perplexity",
        "base_url": "https://api.perplexity.ai",
        "api_key_env": "PERPLEXITY_API_KEY",
        "default_model": "sonar",
        "mode": "compatible",
    },
    "mistral": {
        "en": "Mistral",
        "ar": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "default_model": "mistral-large-latest",
        "mode": "compatible",
    },
    "xai": {
        "en": "xAI",
        "ar": "xAI",
        "base_url": "https://api.x.ai/v1",
        "api_key_env": "XAI_API_KEY",
        "default_model": "grok-2-latest",
        "mode": "compatible",
    },
    "deepseek": {
        "en": "DeepSeek",
        "ar": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "api_key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-chat",
        "mode": "compatible",
    },
    "moonshot": {
        "en": "Moonshot",
        "ar": "Moonshot",
        "base_url": "https://api.moonshot.ai/v1",
        "api_key_env": "MOONSHOT_API_KEY",
        "default_model": "moonshot-v1-8k",
        "mode": "compatible",
    },
    "siliconflow": {
        "en": "SiliconFlow",
        "ar": "SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "api_key_env": "SILICONFLOW_API_KEY",
        "default_model": "Qwen/Qwen2.5-72B-Instruct",
        "mode": "compatible",
    },
    "novita": {
        "en": "Novita",
        "ar": "Novita",
        "base_url": "https://api.novita.ai/v3/openai",
        "api_key_env": "NOVITA_API_KEY",
        "default_model": "meta-llama/Meta-Llama-3.1-70B-Instruct",
        "mode": "compatible",
    },
    "azure": {
        "en": "Azure OpenAI",
        "ar": "Azure OpenAI",
        "base_url": "",
        "api_key_env": "AZURE_OPENAI_API_KEY",
        "default_model": OPENAI_MODEL,
        "mode": "manual",
    },
    "anthropic": {
        "en": "Anthropic",
        "ar": "Anthropic",
        "base_url": "",
        "api_key_env": "ANTHROPIC_API_KEY",
        "default_model": "claude-3-5-sonnet-latest",
        "mode": "manual",
    },
    "huggingface": {
        "en": "Hugging Face",
        "ar": "Hugging Face",
        "base_url": "",
        "api_key_env": "HUGGINGFACE_API_TOKEN",
        "default_model": "",
        "mode": "manual",
    },
    "cohere": {
        "en": "Cohere",
        "ar": "Cohere",
        "base_url": "",
        "api_key_env": "COHERE_API_KEY",
        "default_model": "command-r-plus",
        "mode": "manual",
    },
    "custom": {
        "en": "Custom OpenAI-compatible",
        "ar": "مخصص متوافق مع OpenAI",
        "base_url": "",
        "api_key_env": "",
        "default_model": OPENAI_MODEL,
        "mode": "manual",
    },
}

AI_PROVIDER_ALIASES = {
    "free": "ollama",
    "local": "ollama",
    "freeai": "ollama",
    "ollama-free": "ollama",
    "gemini-ai": "gemini",
    "default": "auto",
    "open-ai": "openai",
    "openrouterai": "openrouter",
    "togetherai": "together",
    "deep infra": "deepinfra",
    "silicon flow": "siliconflow",
    "huggingface": "huggingface",
    "hf": "huggingface",
    "azure-openai": "azure",
}

FREE_MODEL_CATALOG = {
    "qwen2.5:14b-instruct": {
        "en": "Qwen2.5 14B Instruct",
        "ar": "كوين 2.5 - 14B Instruct",
        "desc_en": "Stronger multilingual free choice for Arabic + English study and quiz work.",
        "desc_ar": "خيار مجاني أقوى متعدد اللغات للعربية والإنجليزية في الدراسة والاختبارات.",
        "recommended": True,
    },
    "gemma3:12b": {
        "en": "Gemma 3 12B",
        "ar": "جيما 3 - 12B",
        "desc_en": "Google's stronger free local model with wide multilingual coverage.",
        "desc_ar": "نموذج محلي قوي من Google بتغطية لغات واسعة.",
        "recommended": True,
    },
    "gemma3:4b": {
        "en": "Gemma 3 4B",
        "ar": "جيما 3 - 4B",
        "desc_en": "A lighter Gemma 3 option for smaller machines.",
        "desc_ar": "خيار أخف من Gemma 3 للأجهزة الأصغر.",
        "recommended": False,
    },
    "qwen2.5:7b": {
        "en": "Qwen2.5 7B",
        "ar": "كوين 2.5 - 7B",
        "desc_en": "Lighter fallback for smaller machines and faster local replies.",
        "desc_ar": "خيار أخف للأجهزة الأضعف واستجابات محلية أسرع.",
        "recommended": False,
    },
    "llama3.1:8b": {
        "en": "Llama 3.1 8B",
        "ar": "لاما 3.1 - 8B",
        "desc_en": "Strong general-purpose open model for mixed tasks.",
        "desc_ar": "نموذج مفتوح قوي للاستخدام العام والمهام المختلطة.",
        "recommended": False,
    },
}

FREE_MODEL_ALIASES = {
    "qwen": "qwen2.5:14b-instruct",
    "qwen2.5": "qwen2.5:14b-instruct",
    "qwen2.5:14b": "qwen2.5:14b-instruct",
    "qwen2.5:14b-instruct": "qwen2.5:14b-instruct",
    "qwen2.5:7b-instruct": "qwen2.5:7b",
    "llama": "llama3.1:8b",
    "llama3.1": "llama3.1:8b",
    "llama3.1:8b-instruct": "llama3.1:8b",
    "gemma": "gemma3:12b",
    "gemma3": "gemma3:12b",
    "gemma3:4b": "gemma3:4b",
    "gemma3:12b": "gemma3:12b",
}

AI_STOPWORDS_EN: Set[str] = {
    "the", "and", "for", "with", "that", "this", "from", "into", "about", "what", "when",
    "where", "which", "who", "whom", "whose", "why", "how", "your", "their", "there",
    "have", "has", "had", "was", "were", "are", "is", "be", "been", "being", "will",
    "can", "could", "should", "would", "may", "might", "must", "not", "but", "or", "as",
    "an", "a", "of", "to", "in", "on", "by", "at", "if", "it", "its", "we", "you", "they",
}

AI_STOPWORDS_AR: Set[str] = {
    "في", "من", "على", "إلى", "الى", "عن", "ما", "ماذا", "متى", "أين", "كيف", "لماذا",
    "هذا", "هذه", "ذلك", "تلك", "الذي", "التي", "الذين", "اللاتي", "اللواتي", "ال", "ثم",
    "مع", "كان", "كانت", "يكون", "تكون", "أن", "إن", "لكن", "أو", "و", "لا", "نعم",
}

LOCAL_GENERIC_DISTRACTORS: Dict[str, List[str]] = {
    "en": [
        "A different concept",
        "An unrelated detail",
        "A background fact",
        "A minor example",
        "A broad idea",
    ],
    "ar": [
        "مفهوم مختلف",
        "تفصيل غير مرتبط",
        "معلومة خلفية",
        "مثال جانبي",
        "فكرة عامة",
    ],
}

# -*- coding: utf-8 -*-
"""
Localization and internationalization dictionary and helpers.
"""

from typing import Any, Dict

TEXTS: Dict[str, Dict[str, str]] = {
    "start": {
        "en": "MCQ Bot is ready. Send formatted MCQs, or use /ai and /quizify for AI-generated quizzes.",
        "ar": "بوت الاختبارات جاهز. أرسل أسئلة بصيغة MCQ، أو استخدم /ai و /quizify لتوليد اختبارات بالذكاء الاصطناعي."
    },
    "help": {
        "en": "Usage:\n- Private: send MCQ text directly.\n- Groups: reply to the bot, mention the bot, or use commands.\n- Channels: make the bot admin, then post MCQ text or start the post with ai: to generate quizzes.\n\nCommands:\n/settings - show current settings\n/controls - open the full control panel\n/stats - show usage stats\n/setchannel <chat_id|@channel|here> - set default publishing target\n/publishhere - set current chat as your default target\n/clearchannel - clear default target\n/toggledelete - toggle deleting source messages after publishing\n/toggleai - enable or disable AI for your account\n/setmodel <model> - override AI model and auto-detect the provider when possible\n/provider <name> - choose the AI provider preset\n/providers - list the available AI provider presets\n/freeai - switch to the best available free AI profile\n/freemodels - list the recommended free local models\n/freemodel <name> - choose a free local model\n/aidiag - show AI status and quick fixes\n/setcount <1-10> - default AI quiz count\n/language <auto|ar|en> - choose the bot language\n/lang <auto|ar|en> - shortcut for /language\n/specialty <text|clear> - set AI specialty or domain\n/delivery <fast|rich> - prioritize speed or richer buttons\n/sharemode <telegram|web|both> - choose how share buttons appear\n/toggleexplain - show or hide explanation button\n/toggleconfirm - show or hide confirmation message\n/tool <mode> - choose the default AI tool\n/tools - list the available AI tools\n/ask [tool] <text> - run a smart AI tool on text\n/funmode <on|off> - enable or disable group entertainment breaks\n/funrate <1-30> - set how often group breaks appear\n/funstyle <mixed|trivia|joke|riddle|icebreaker|poll|study> - choose break style\n/health - runtime health and queue status\n/examples - show supported MCQ formats\n/ai <topic> - generate quizzes from a topic\n/study <text> - create a study pack from the text\n/quizify <text> - convert text into quizzes, or reply to a message with /quizify\n\nQuick AI shortcuts:\n- ai: biology chapter 3\n- quizify: paste lesson text here",
        "ar": "طريقة الاستخدام:\n- في الخاص: أرسل نص الأسئلة مباشرة.\n- في المجموعات: رُد على البوت أو اذكره أو استخدم الأوامر.\n- في القنوات: اجعل البوت مشرفاً، ثم انشر نص MCQ أو ابدأ المنشور بـ ai: لتوليد الاختبارات.\n\nالأوامر:\n/settings - عرض الإعدادات الحالية\n/controls - فتح لوحة التحكم الكاملة\n/stats - عرض الإحصاءات\n/setchannel <chat_id|@channel|here> - تعيين جهة النشر الافتراضية\n/publishhere - تعيين الدردشة الحالية كوجهة افتراضية\n/clearchannel - حذف الوجهة الافتراضية\n/toggledelete - تبديل حذف رسالة المصدر بعد النشر\n/toggleai - تشغيل أو إيقاف الذكاء الاصطناعي لحسابك\n/setmodel <model> - تغيير نموذج الذكاء الاصطناعي مع اكتشاف المزوّد المناسب تلقائياً إذا أمكن\n/provider <name> - اختيار مزود الذكاء الاصطناعي\n/providers - عرض مزودات الذكاء الاصطناعي المتاحة\n/freeai - التبديل إلى أفضل ملف ذكاء مجاني متاح\n/freemodels - عرض النماذج المحلية المجانية المقترحة\n/freemodel <name> - اختيار نموذج محلي مجاني\n/aidiag - عرض حالة الذكاء الاصطناعي والإصلاحات السريعة\n/setcount <1-10> - عدد الاختبارات الافتراضي للتوليد\n/language <auto|ar|en> - اختيار لغة البوت\n/lang <auto|ar|en> - اختصار لأمر /language\n/specialty <text|clear> - ضبط تخصص الذكاء الاصطناعي\n/delivery <fast|rich> - تفضيل السرعة أو المزايا الغنية\n/sharemode <telegram|web|both> - اختيار شكل أزرار المشاركة\n/toggleexplain - إظهار أو إخفاء زر الشرح\n/toggleconfirm - إظهار أو إخفاء رسالة التأكيد\n/tool <mode> - اختيار أداة الذكاء الاصطناعي الافتراضية\n/tools - عرض أدوات الذكاء الاصطناعي المتاحة\n/ask [tool] <text> - تشغيل أداة ذكية على النص\n/funmode <on|off> - تفعيل أو إيقاف الفواصل الترفيهية في المجموعات\n/funrate <1-30> - تحديد معدل ظهور الفاصل الترفيهي\n/funstyle <mixed|trivia|joke|riddle|icebreaker|poll|study> - اختيار نمط الفاصل\n/health - حالة التشغيل والطوابير\n/examples - عرض صيغ الأسئلة المدعومة\n/ai <topic> - توليد اختبارات من موضوع\n/study <text> - إنشاء حزمة مذاكرة من النص\n/quizify <text> - تحويل نص إلى اختبارات، أو رُد على رسالة باستخدام /quizify\n\nاختصارات سريعة:\n- ai: الفصل الثالث أحياء\n- quizify: الصق النص هنا"
    },
    "no_q": {
        "en": "No MCQ detected. Send a valid MCQ block or use /ai or /quizify.",
        "ar": "لم أجد سؤال MCQ صالحاً. أرسل صيغة صحيحة أو استخدم /ai أو /quizify."
    },
    "invalid_format": {
        "en": "Invalid MCQ format or limits exceeded.",
        "ar": "صيغة الأسئلة غير صحيحة أو تجاوزت الحدود المسموح بها."
    },
    "queue_full": {
        "en": "Queue is full. Try fewer questions or wait a moment.",
        "ar": "قائمة الإرسال ممتلئة حالياً. حاول بعدد أقل أو انتظر قليلاً."
    },
    "quiz_sent": {
        "en": "Quiz published.",
        "ar": "تم نشر الاختبار."
    },
    "share_quiz": {
        "en": "Share Quiz",
        "ar": "مشاركة الاختبار"
    },
    "repost_quiz": {
        "en": "Repost",
        "ar": "إعادة النشر"
    },
    "show_explanation": {
        "en": "Explanation",
        "ar": "الشرح"
    },
    "stats": {
        "en": "Your activity:\n- Private/generated by you: {private_count}\n- Total published across targets: {total_targets}\n- Current default target: {target}",
        "ar": "نشاطك:\n- أسئلة خاصة/منشأة بواسطتك: {private_count}\n- إجمالي ما تم نشره عبر الوجهات: {total_targets}\n- الوجهة الافتراضية الحالية: {target}"
    },
    "settings": {
        "en": "Settings:\n- Default target: {target}\n- Delete source after publish: {delete_source}\n- AI available: {ai_available}\n- Offline fallback: {offline_fallback}\n- AI enabled for you: {ai_enabled}\n- AI model: {ai_model}\n- AI provider: {ai_provider}\n- AI batch size: {ai_count}\n- Interface language: {language}\n- AI specialty: {specialty}\n- AI tool: {ai_tool}\n- Delivery mode: {delivery_mode}\n- Share mode: {share_mode}\n- Preserve question order: {preserve_order}\n- Web preview URL configured: {web_preview}\n- Show explanation button: {show_explanation}\n- Confirmation message: {confirmation}\n- Group fun breaks: {fun_breaks}\n- Fun interval: {fun_interval}\n- Fun style: {fun_style}",
        "ar": "الإعدادات:\n- الوجهة الافتراضية: {target}\n- حذف رسالة المصدر بعد النشر: {delete_source}\n- توفر خدمة الذكاء الاصطناعي: {ai_available}\n- وضع الاحتياطي المحلي: {offline_fallback}\n- الذكاء الاصطناعي مفعل لك: {ai_enabled}\n- نموذج الذكاء الاصطناعي: {ai_model}\n- مزود الذكاء الاصطناعي: {ai_provider}\n- عدد الأسئلة الافتراضي: {ai_count}\n- لغة الواجهة: {language}\n- تخصص الذكاء الاصطناعي: {specialty}\n- أداة الذكاء الاصطناعي: {ai_tool}\n- وضع الإرسال: {delivery_mode}\n- وضع المشاركة: {share_mode}\n- الحفاظ على ترتيب الأسئلة: {preserve_order}\n- رابط المعاينة الخارجية مضبوط: {web_preview}\n- إظهار زر الشرح: {show_explanation}\n- رسالة التأكيد: {confirmation}\n- الفواصل الترفيهية في المجموعات: {fun_breaks}\n- معدل الفاصل: {fun_interval}\n- نمط الفاصل: {fun_style}"
    },
    "target_set": {
        "en": "Default target updated to: {target}",
        "ar": "تم تحديث الوجهة الافتراضية إلى: {target}"
    },
    "target_cleared": {
        "en": "Default target cleared. Publishing will use the current chat.",
        "ar": "تم حذف الوجهة الافتراضية. سيستخدم النشر الدردشة الحالية."
    },
    "delete_enabled": {
        "en": "Source message deletion is now enabled.",
        "ar": "تم تفعيل حذف رسالة المصدر بعد النشر."
    },
    "delete_disabled": {
        "en": "Source message deletion is now disabled.",
        "ar": "تم إيقاف حذف رسالة المصدر بعد النشر."
    },
    "ai_disabled_user": {
        "en": "AI is disabled for your account. Use /toggleai to enable it.",
        "ar": "الذكاء الاصطناعي متوقف لحسابك. استخدم /toggleai لتفعيله."
    },
    "ai_disabled_global": {
        "en": "AI is not available. Add OPENAI_API_KEY and install dependencies first.",
        "ar": "الذكاء الاصطناعي غير متاح حالياً. أضف OPENAI_API_KEY وثبّت الاعتماديات أولاً."
    },
    "ai_processing": {
        "en": "Generating quizzes with AI...",
        "ar": "جارٍ توليد الاختبارات بالذكاء الاصطناعي..."
    },
    "ai_processing_offline": {
        "en": "Generating quizzes with the offline fallback engine...",
        "ar": "جارٍ توليد الاختبارات بالمحرك المحلي الاحتياطي..."
    },
    "ai_done": {
        "en": "AI created {count} quiz item(s) and queued them for publishing.",
        "ar": "أنشأ الذكاء الاصطناعي {count} اختباراً وتمت إضافتها إلى قائمة النشر."
    },
    "ai_error": {
        "en": "AI request failed: {reason}",
        "ar": "فشل طلب الذكاء الاصطناعي: {reason}"
    },
    "ai_fallback_used": {
        "en": "No remote AI endpoint was available, so the offline study engine handled this request.",
        "ar": "لم يكن هناك مزود ذكاء اصطناعي متاح، لذلك عالج المحرك المحلي هذا الطلب."
    },
    "tool_processing": {
        "en": "Working with the selected AI tool...",
        "ar": "جارٍ العمل باستخدام أداة الذكاء الاصطناعي المختارة..."
    },
    "tool_processing_offline": {
        "en": "Working with the offline fallback engine...",
        "ar": "جارٍ العمل بالمحرك المحلي الاحتياطي..."
    },
    "tool_done": {
        "en": "AI response ready.",
        "ar": "أصبح رد الذكاء الاصطناعي جاهزاً."
    },
    "tool_error": {
        "en": "AI tool failed: {reason}",
        "ar": "فشلت أداة الذكاء الاصطناعي: {reason}"
    },
    "ai_usage_topic": {
        "en": "Usage: /ai <topic> or send a message starting with ai:",
        "ar": "الاستخدام: /ai <topic> أو أرسل رسالة تبدأ بـ ai:"
    },
    "ai_usage_text": {
        "en": "Usage: /quizify <text> or reply to a message with /quizify",
        "ar": "الاستخدام: /quizify <text> أو رُد على رسالة باستخدام /quizify"
    },
    "ai_usage_tool": {
        "en": "Usage: /ask [tool] <text>",
        "ar": "الاستخدام: /ask [tool] <text>"
    },
    "model_set": {
        "en": "AI model updated to: {model}",
        "ar": "تم تحديث نموذج الذكاء الاصطناعي إلى: {model}"
    },
    "provider_set": {
        "en": "AI provider updated to: {provider}",
        "ar": "تم تحديث مزود الذكاء الاصطناعي إلى: {provider}"
    },
    "provider_free": {
        "en": "Free AI profile selected: {provider} ({model}). If the chosen backend is unavailable, the offline fallback engine will handle requests.",
        "ar": "تم اختيار ملف الذكاء المجاني: {provider} ({model}). وإذا كان الخلفي المختار غير متاح، فسيعالج المحرك الاحتياطي المحلي الطلبات."
    },
    "provider_missing": {
        "en": "This provider needs its API key or endpoint configured in environment variables.",
        "ar": "هذا المزود يحتاج إلى إعداد مفتاح API أو نقطة نهاية في متغيرات البيئة."
    },
    "count_set": {
        "en": "Default AI batch size updated to: {count}",
        "ar": "تم تحديث العدد الافتراضي لأسئلة الذكاء الاصطناعي إلى: {count}"
    },
    "language_set": {
        "en": "Bot language updated to: {language}",
        "ar": "تم تحديث لغة البوت إلى: {language}"
    },
    "usage_language": {
        "en": "Usage: /language <auto|ar|en>",
        "ar": "الاستخدام: /language <auto|ar|en>"
    },
    "specialty_set": {
        "en": "AI specialty updated to: {specialty}",
        "ar": "تم تحديث تخصص الذكاء الاصطناعي إلى: {specialty}"
    },
    "usage_specialty": {
        "en": "Usage: /specialty <text|clear>",
        "ar": "الاستخدام: /specialty <text|clear>"
    },
    "delivery_set": {
        "en": "Delivery mode updated to: {mode}",
        "ar": "تم تحديث وضع الإرسال إلى: {mode}"
    },
    "usage_delivery": {
        "en": "Usage: /delivery <fast|rich>",
        "ar": "الاستخدام: /delivery <fast|rich>"
    },
    "sharemode_set": {
        "en": "Share mode updated to: {mode}",
        "ar": "تم تحديث وضع المشاركة إلى: {mode}"
    },
    "usage_sharemode": {
        "en": "Usage: /sharemode <telegram|web|both>",
        "ar": "الاستخدام: /sharemode <telegram|web|both>"
    },
    "toggle_explain_on": {
        "en": "Explanation button is now enabled.",
        "ar": "تم تفعيل زر الشرح."
    },
    "toggle_explain_off": {
        "en": "Explanation button is now disabled.",
        "ar": "تم إيقاف زر الشرح."
    },
    "toggle_confirm_on": {
        "en": "Confirmation message is now enabled.",
        "ar": "تم تفعيل رسالة التأكيد."
    },
    "toggle_confirm_off": {
        "en": "Confirmation message is now disabled.",
        "ar": "تم إيقاف رسالة التأكيد."
    },
    "toggle_ai_on": {
        "en": "AI is now enabled for your account.",
        "ar": "تم تفعيل الذكاء الاصطناعي لحسابك."
    },
    "toggle_ai_off": {
        "en": "AI is now disabled for your account.",
        "ar": "تم إيقاف الذكاء الاصطناعي لحسابك."
    },
    "quiz_loaded": {
        "en": "Saved quiz loaded into your chat.",
        "ar": "تم تحميل الاختبار المحفوظ إلى دردشتك."
    },
    "quiz_missing": {
        "en": "Quiz not found or no longer available.",
        "ar": "الاختبار غير موجود أو لم يعد متاحاً."
    },
    "explanation_missing": {
        "en": "No explanation is stored for this quiz.",
        "ar": "لا يوجد شرح محفوظ لهذا الاختبار."
    },
    "open_preview": {
        "en": "Preview",
        "ar": "معاينة"
    },
    "share_everywhere": {
        "en": "Share Everywhere",
        "ar": "مشاركة خارجية"
    },
    "examples": {
        "en": "Supported formats:\n1. Standard lines:\nQ: What is 2+2?\nA) 3\nB) 4\nC) 5\nAnswer: B\n\n2. Arabic labels:\nس: عاصمة مصر؟\nأ) القاهرة\nب) الرياض\nج) دمشق\nالإجابة: أ\n\n3. Numbered options:\nQuestion: Largest planet?\n1) Mars\n2) Jupiter\n3) Venus\nCorrect Answer: 2\n\n4. True/False:\nQ: The sun is a star.\nAnswer: True\n\n5. Bullet options:\nWhat is H2O?\n- Oxygen\n- Water\n- Hydrogen\nAnswer: Water",
        "ar": "الصيغ المدعومة:\n1. الصيغة العادية:\nس: كم يساوي 2+2؟\nأ) 3\nب) 4\nج) 5\nالإجابة: ب\n\n2. بصيغة إنجليزية:\nQ: Largest planet?\nA) Mars\nB) Jupiter\nC) Venus\nAnswer: B\n\n3. خيارات مرقمة:\nQuestion: Largest planet?\n1) Mars\n2) Jupiter\n3) Venus\nCorrect Answer: 2\n\n4. صح أو خطأ:\nس: الشمس نجم.\nالإجابة: صح\n\n5. خيارات بنقاط:\nما هو H2O؟\n- أكسجين\n- ماء\n- هيدروجين\nالإجابة: ماء"
    },
    "health": {
        "en": "Runtime health:\n- Memory: {memory_mb} MB\n- Active target queues: {active_targets}\n- Pending items: {pending_items}\n- Concurrent updates: {concurrent_updates}\n- Per-target send concurrency: {per_target}\n- Global send limit: {global_limit}",
        "ar": "حالة التشغيل:\n- الذاكرة: {memory_mb} MB\n- عدد وجهات الإرسال النشطة: {active_targets}\n- العناصر المعلقة: {pending_items}\n- التحديثات المتزامنة: {concurrent_updates}\n- التوازي لكل وجهة: {per_target}\n- الحد العالمي للإرسال: {global_limit}"
    },
    "unsupported": {
        "en": "Unsupported action.",
        "ar": "إجراء غير مدعوم."
    },
    "usage_setchannel": {
        "en": "Usage: /setchannel <chat_id|@channel|here>",
        "ar": "الاستخدام: /setchannel <chat_id|@channel|here>"
    },
    "usage_setmodel": {
        "en": "Usage: /setmodel <model-id>",
        "ar": "الاستخدام: /setmodel <model-id>"
    },
    "usage_provider": {
        "en": "Usage: /provider <name>",
        "ar": "الاستخدام: /provider <name>"
    },
    "usage_providers": {
        "en": "Usage: /providers",
        "ar": "الاستخدام: /providers"
    },
    "usage_freeai": {
        "en": "Usage: /freeai",
        "ar": "الاستخدام: /freeai"
    },
    "usage_setcount": {
        "en": "Usage: /setcount <1-10>",
        "ar": "الاستخدام: /setcount <1-10>"
    },
    "usage_tool": {
        "en": "Usage: /tool <mode>",
        "ar": "الاستخدام: /tool <mode>"
    },
    "usage_ask": {
        "en": "Usage: /ask [tool] <text>",
        "ar": "الاستخدام: /ask [tool] <text>"
    },
    "usage_funmode": {
        "en": "Usage: /funmode <on|off>",
        "ar": "الاستخدام: /funmode <on|off>"
    },
    "usage_funrate": {
        "en": "Usage: /funrate <1-30>",
        "ar": "الاستخدام: /funrate <1-30>"
    },
    "usage_funstyle": {
        "en": "Usage: /funstyle <mixed|trivia|joke|riddle|icebreaker|poll|study>",
        "ar": "الاستخدام: /funstyle <mixed|trivia|joke|riddle|icebreaker|poll|study>"
    },
    "tool_set": {
        "en": "AI tool updated to: {tool}",
        "ar": "تم تحديث أداة الذكاء الاصطناعي إلى: {tool}"
    },
    "funmode_set": {
        "en": "Group entertainment breaks are now {state}.",
        "ar": "أصبحت الفواصل الترفيهية في المجموعات {state}."
    },
    "funrate_set": {
        "en": "Fun interval updated to every {count} quizzes.",
        "ar": "تم تحديث معدل الفاصل إلى كل {count} أسئلة."
    },
    "funstyle_set": {
        "en": "Fun style updated to: {style}",
        "ar": "تم تحديث نمط الفاصل إلى: {style}"
    },
    "tools_header": {
        "en": "Available AI tools:",
        "ar": "أدوات الذكاء الاصطناعي المتاحة:"
    },
    "fun_break_header": {
        "en": "Group break",
        "ar": "فاصل للمجموعة"
    },
    "fun_break_label": {
        "en": "Quick break",
        "ar": "فاصل سريع"
    },
    "providers_header": {
        "en": "Available AI provider presets:",
        "ar": "مزوّدات الذكاء الاصطناعي المتاحة:"
    },
    "fun_help": {
        "en": "Group entertainment controls:\n- /funmode on|off\n- /funrate 1-30\n- /funstyle mixed|trivia|joke|riddle|icebreaker|poll|study\nWhen enabled, the bot inserts a short break after a few quizzes in groups.",
        "ar": "تحكم الفواصل الترفيهية في المجموعات:\n- /funmode on|off\n- /funrate 1-30\n- /funstyle mixed|trivia|joke|riddle|icebreaker|poll|study\nعند التفعيل يضيف البوت فاصلًا قصيراً بعد عدة أسئلة في المجموعات."
    },
    "target_unreachable": {
        "en": "The bot could not access that target. Add the bot there first or check the ID/username.",
        "ar": "لم يتمكن البوت من الوصول إلى هذه الوجهة. أضف البوت هناك أولاً أو تحقق من المعرّف/الاسم."
    },
    "free_ai_help": {
        "en": "Free AI on hosting works best with Gemini if you set GEMINI_API_KEY. For local free AI, use Ollama with OPENAI_BASE_URL=http://localhost:11434/v1 and models like qwen2.5:14b-instruct or gemma3:12b. If no live backend is available, the bot falls back to an offline study engine for quizzes and study tools.",
        "ar": "أفضل خيار مجاني على الاستضافة هو Gemini إذا أضفت GEMINI_API_KEY. وللذكاء المجاني المحلي استخدم Ollama مع OPENAI_BASE_URL=http://localhost:11434/v1 ونماذج مثل qwen2.5:14b-instruct أو gemma3:12b. وإذا لم يكن هناك خلفي حي، فسيستخدم البوت محركاً محلياً احتياطياً للأسئلة وأدوات الدراسة."
    },
    "free_models_header": {
        "en": "Recommended free local models and hosted free option:",
        "ar": "أفضل النماذج المحلية المجانية وخيار الاستضافة المجاني:"
    },
    "usage_freemodels": {
        "en": "Usage: /freemodels",
        "ar": "الاستخدام: /freemodels"
    },
    "usage_freemodel": {
        "en": "Usage: /freemodel <name>",
        "ar": "الاستخدام: /freemodel <name>"
    },
    "freemodel_set": {
        "en": "Free local model updated to: {model}",
        "ar": "تم تحديث النموذج المحلي المجاني إلى: {model}"
    },
    "usage_aidiag": {
        "en": "Usage: /aidiag",
        "ar": "الاستخدام: /aidiag"
    },
    "study_help": {
        "en": "Study mode creates a compact study pack from your text:\n- summary\n- key points\n- flashcards\n- quick quiz\n\nUse /study <text> or reply to a message with /study.",
        "ar": "وضع الدراسة ينشئ حزمة مذاكرة مختصرة من النص:\n- ملخص\n- نقاط مهمة\n- بطاقات\n- اختبار سريع\n\nاستخدم /study <text> أو رُد على رسالة باستخدام /study."
    },
    "usage_study": {
        "en": "Usage: /study <text> or reply to a message with /study",
        "ar": "الاستخدام: /study <text> أو رُد على رسالة باستخدام /study"
    },
    "study_set": {
        "en": "Study pack ready.",
        "ar": "حزمة الدراسة جاهزة."
    },
    "ai_status_header": {
        "en": "AI status and quick fixes:",
        "ar": "حالة الذكاء الاصطناعي والإصلاحات السريعة:"
    },
    "export_help": {
        "en": "📤 *Export Quizzes*\n\nChoose the format to export your quizzes:\n• *JSON*: Full data backup\n• *CSV*: For Excel & Google Sheets\n• *GIFT*: For Moodle & Anki\n• *Text*: Clean copyable format",
        "ar": "📤 *تصدير الأسئلة والاختبارات*\n\nاختر الصيغة التي ترغب في تصدير الأسئلة بها:\n• *JSON*: نسخة احتياطية برمجية شاملة\n• *CSV*: متوافق مع Excel وجداول Google\n• *GIFT*: متوافق مع Moodle وبطاقات Anki\n• *نص*: صيغة نصية مرتبة وسهلة النسخ"
    },
    "export_empty": {
        "en": "⚠️ No quizzes found in your history to export.",
        "ar": "⚠️ لم يتم العثور على أي أسئلة محفوظة في سجلك لتصديرها."
    },
    "export_success": {
        "en": "✅ Export complete! Found {count} quizzes.",
        "ar": "✅ تم التصدير بنجاح! تم العثور على {count} سؤال."
    },
    "exam_intro": {
        "en": "🎯 *Interactive Quiz Mode*\n\nYou will be presented with {count} questions one by one. Answer each question to proceed!\nReady?",
        "ar": "🎯 *وضع الاختبار التفاعلي*\n\nسيتم عرض {count} أسئلة عليك واحداً تلو الآخر للإجابة عليها مباشرة!\nهل أنت مستعد؟"
    },
    "exam_finished": {
        "en": "🎉 *Exam Completed!*\n\n• Questions: {total}\n• Correct: {correct} ✅\n• Wrong: {wrong} ❌\n• Score: {score}%\n\n{feedback}",
        "ar": "🎉 *اكتمل الاختبار!*\n\n• إجمالي الأسئلة: {total}\n• الإجابات الصحيحة: {correct} ✅\n• الإجابات الخاطئة: {wrong} ❌\n• النتيجة: {score}%\n\n{feedback}"
    },
    "document_parsing": {
        "en": "📄 Reading document: {filename}...\nExtracting questions and text...",
        "ar": "📄 جاري قراءة الملف: {filename}...\nواستخراج الأسئلة والنصوص..."
    },
    "document_success": {
        "en": "✅ Successfully parsed {count} questions from document! Queuing...",
        "ar": "✅ تم استخراج {count} أسئلة بنجاح من المستند! جاري النشر..."
    },
    "document_empty": {
        "en": "⚠️ Could not find any valid MCQs in the uploaded file.",
        "ar": "⚠️ لم يتم العثور على أي أسئلة اختيار من متعدد صالحة في الملف المرفق."
    },
    "batch_success": {
        "en": "✅ Found and queued {count} quizzes successfully.",
        "ar": "✅ تم استخراج وجدولة {count} أسئلة بنجاح."
    },
    "menu_title": {
        "en": "🧭 *Main Navigation Menu*\n\nSelect a section below to get started:",
        "ar": "🧭 *القائمة الرئيسية التفاعلية*\n\nاختر أحد الأقسام أدناه للبدء والتحكم:"
    }
}


def get_text(key: str, lang: str = "ar", **kwargs: Any) -> str:
    """Get localized text with fallback and formatting."""
    pack = TEXTS.get(key, {})
    chosen_lang = "ar" if (lang or "").strip().lower() == "ar" else "en"
    template = pack.get(chosen_lang) or pack.get("en") or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except Exception:
        return template

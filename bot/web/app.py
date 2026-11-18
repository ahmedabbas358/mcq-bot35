"""
Web server for external quiz preview pages and platform keep-alive pings.
Includes interactive browser quiz testing with sound effects, confetti animations, copy tools, and RTL support.
"""

import contextlib
import html
import json
import logging
import os
import sqlite3
from threading import Thread
from typing import Optional

try:
    from flask import Flask, jsonify, redirect, request
    app = Flask(__name__)
except ImportError:
    Flask = None
    jsonify = None
    redirect = None
    request = None
    app = None

from bot.config import (
    DB_PATH,
    PORT,
    TELEGRAM_BOT_USERNAME,
    resolve_public_base_url,
)
from bot.utils.helpers import has_arabic, quote_plus

logger = logging.getLogger(__name__)


def fetch_quiz_record(quiz_id: str) -> Optional[dict]:
    """Fetch quiz from sqlite synchronously for web rendering."""
    if not os.path.exists(DB_PATH):
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT question, options, correct_option, explanation FROM quizzes WHERE quiz_id=?",
            (quiz_id,),
        ).fetchone()
        if row is None:
            return None
        try:
            options = json.loads(row["options"] or "[]")
        except Exception:
            options = [part for part in (row["options"] or "").split(":::") if part]
        return {
            "question": row["question"] or "",
            "options": options,
            "correct_option": int(row["correct_option"] or 0),
            "explanation": row["explanation"] or "",
        }
    except Exception as exc:
        logger.warning("Error fetching quiz for web preview %s: %s", quiz_id, exc)
        return None
    finally:
        conn.close()


def render_page(title: str, body: str, description: str = "", is_rtl: bool = False) -> str:
    safe_title = html.escape(title)
    safe_description = html.escape(description or title)
    direction = "rtl" if is_rtl else "ltr"
    lang_code = "ar" if is_rtl else "en"

    return f"""<!doctype html>
<html lang="{lang_code}" dir="{direction}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} - MCQ Bot</title>
  <meta name="description" content="{safe_description}">
  <meta property="og:title" content="{safe_title}">
  <meta property="og:description" content="{safe_description}">
  <meta property="og:type" content="website">
  <meta property="twitter:card" content="summary_large_image">
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Cairo:wght@400;600;700;800&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg: #0b1120;
      --card-bg: #151f32;
      --border: #24324d;
      --text: #f8fafc;
      --text-muted: #94a3b8;
      --primary: #3b82f6;
      --primary-hover: #2563eb;
      --success: #10b981;
      --success-bg: rgba(16, 185, 129, 0.15);
      --danger: #ef4444;
      --danger-bg: rgba(239, 68, 68, 0.15);
      --font-family: {'"Cairo", sans-serif' if is_rtl else '"Inter", sans-serif'};
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: var(--font-family);
      background-color: var(--bg);
      background-image: radial-gradient(at 0% 0%, rgba(59, 130, 246, 0.1) 0px, transparent 50%),
                        radial-gradient(at 100% 100%, rgba(16, 185, 129, 0.08) 0px, transparent 50%);
      color: var(--text);
      min-height: 100vh;
      display: flex;
      flex-direction: column;
      align-items: center;
      justify-content: center;
      padding: 24px 16px;
    }}
    .wrap {{ width: 100%; max-width: 680px; margin: 0 auto; position: relative; z-index: 10; }}
    .card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: 24px;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
      padding: 36px 30px;
      backdrop-filter: blur(12px);
    }}
    .top-meta {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 20px;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 6px;
      padding: 6px 14px;
      border-radius: 20px;
      background: rgba(59, 130, 246, 0.15);
      color: #60a5fa;
      font-size: 13px;
      font-weight: 700;
    }}
    .sound-btn {{
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid var(--border);
      color: var(--text-muted);
      border-radius: 999px;
      padding: 5px 12px;
      font-size: 12px;
      cursor: pointer;
      font-family: inherit;
      transition: all 0.2s;
    }}
    .sound-btn:hover {{ color: var(--text); border-color: var(--text-muted); }}
    h1 {{
      font-size: clamp(20px, 4vw, 26px);
      line-height: 1.45;
      margin-bottom: 24px;
      font-weight: 700;
    }}
    .options {{
      display: flex;
      flex-direction: column;
      gap: 12px;
      margin-bottom: 24px;
    }}
    .option-btn {{
      display: flex;
      align-items: center;
      gap: 14px;
      padding: 16px 20px;
      border-radius: 14px;
      border: 1.5px solid var(--border);
      background: #111a2e;
      color: var(--text);
      font-family: inherit;
      font-size: 16px;
      cursor: pointer;
      text-align: { 'right' if is_rtl else 'left' };
      transition: all 0.2s ease;
      width: 100%;
    }}
    .option-btn:hover:not(:disabled) {{
      border-color: var(--primary);
      background: #19253d;
      transform: translateY(-2px);
    }}
    .option-btn.selected-correct {{
      border-color: var(--success);
      background: var(--success-bg);
      color: #34d399;
      font-weight: 700;
      box-shadow: 0 0 15px rgba(16, 185, 129, 0.2);
    }}
    .option-btn.selected-wrong {{
      border-color: var(--danger);
      background: var(--danger-bg);
      color: #f87171;
    }}
    .badge {{
      width: 32px;
      height: 32px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.08);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      flex-shrink: 0;
    }}
    .explain-box {{
      display: none;
      margin-top: 20px;
      padding: 18px 22px;
      border-radius: 14px;
      background: rgba(16, 185, 129, 0.1);
      border: 1px dashed var(--success);
      color: #a7f3d0;
      line-height: 1.6;
    }}
    .explain-box.show {{ display: block; animation: fadeIn 0.35s ease; }}
    @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(-6px); }} to {{ opacity: 1; transform: translateY(0); }} }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 24px;
      padding-top: 20px;
      border-top: 1px solid var(--border);
    }}
    .btn {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      padding: 10px 18px;
      border-radius: 999px;
      text-decoration: none;
      font-weight: 600;
      font-size: 14px;
      transition: all 0.2s;
      cursor: pointer;
      font-family: inherit;
    }}
    .btn-primary {{ background: var(--primary); color: white; border: none; }}
    .btn-primary:hover {{ background: var(--primary-hover); transform: translateY(-1px); }}
    .btn-outline {{ background: transparent; color: var(--text-muted); border: 1px solid var(--border); }}
    .btn-outline:hover {{ color: var(--text); border-color: var(--text-muted); }}
    .footer-note {{ text-align: center; margin-top: 24px; color: var(--text-muted); font-size: 13px; }}
    #toast {{
      position: fixed;
      bottom: 24px;
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: #10b981;
      color: white;
      padding: 12px 24px;
      border-radius: 999px;
      font-weight: 600;
      font-size: 14px;
      box-shadow: 0 10px 25px rgba(0,0,0,0.4);
      transition: transform 0.3s cubic-bezier(0.18, 0.89, 0.32, 1.28);
      z-index: 9999;
      pointer-events: none;
    }}
    #toast.show {{ transform: translateX(-50%) translateY(0); }}
    #confetti-canvas {{
      position: fixed;
      top: 0;
      left: 0;
      width: 100%;
      height: 100%;
      pointer-events: none;
      z-index: 9998;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    {body}
    <div class="footer-note">⚡️ Powered by MCQ Bot 35</div>
  </div>
  <div id="toast">✅ {"تم نسخ السؤال بنجاح!" if is_rtl else "Question copied successfully!"}</div>
  <canvas id="confetti-canvas"></canvas>
</body>
</html>"""


if app is not None:
    @app.route("/")
    def home():
        total_quizzes = 0
        total_exams = 0
        if os.path.exists(DB_PATH):
            with contextlib.suppress(Exception):
                conn = sqlite3.connect(DB_PATH)
                total_quizzes = conn.execute("SELECT COUNT(*) FROM quizzes").fetchone()[0]
                total_exams = conn.execute("SELECT COUNT(*) FROM exam_results").fetchone()[0]
                conn.close()

        bot_url = f"https://t.me/{TELEGRAM_BOT_USERNAME}" if TELEGRAM_BOT_USERNAME else "#"
        body = f"""
        <div class="card">
          <div class="top-meta">
            <span class="eyebrow">🚀 MCQ Bot 35 • Web Portal</span>
          </div>
          <h1>منصة الاختبارات التفاعلية والمعاينة السريعة</h1>
          <p style="color: var(--text-muted); line-height: 1.7; margin-bottom: 24px;">
            مرحباً بك في بوابة المعاينة التفاعلية لأسئلة الاختيار من متعدد. يمكنك تجربة حل الأسئلة مباشرة داخل المتصفح،
            والاستماع للتغذية الصوتية الفورية ومشاركة الأسئلة بسهولة.
          </p>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 24px;">
            <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); padding: 18px; border-radius: 16px; text-align: center;">
              <div style="font-size: 32px; font-weight: 800; color: #60a5fa;">{total_quizzes}</div>
              <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">سؤال مسجل في البنك</div>
            </div>
            <div style="background: rgba(255,255,255,0.03); border: 1px solid var(--border); padding: 18px; border-radius: 16px; text-align: center;">
              <div style="font-size: 32px; font-weight: 800; color: #34d399;">{total_exams}</div>
              <div style="font-size: 13px; color: var(--text-muted); margin-top: 4px;">جلسة اختبار مكتملة</div>
            </div>
          </div>
          <div class="actions">
            <a class="btn btn-primary" href="/quiz/random">🎯 تجربة سؤال عشوائي</a>
            <a class="btn btn-outline" href="{bot_url}" target="_blank">✈️ فتح البوت في تيليجرام</a>
          </div>
        </div>
        """
        return render_page("MCQ Bot • Web Portal", body, "Interactive MCQ Quiz Platform", is_rtl=True)

    @app.route("/healthz")
    def healthz():
        return jsonify({"status": "healthy", "service": "mcq-bot-35", "version": "2.0"})

    @app.route("/quiz/random")
    def random_quiz():
        if not os.path.exists(DB_PATH):
            return redirect("/")
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT quiz_id FROM quizzes ORDER BY RANDOM() LIMIT 1").fetchone()
        conn.close()
        if row:
            return redirect(f"/quiz/{row[0]}")
        return redirect("/")

    @app.route("/quiz/<quiz_id>")
    def quiz_preview(quiz_id: str):
        quiz = fetch_quiz_record(quiz_id)
        if quiz is None:
            body = """
            <div class="card">
              <span class="eyebrow" style="color: #ef4444; background: rgba(239, 68, 68, 0.15);">⚠️ Not Found</span>
              <h1>Quiz Not Found</h1>
              <p style="color: var(--text-muted);">This quiz question may have expired or the link is invalid.</p>
              <div class="actions" style="margin-top: 20px;">
                <a class="btn btn-primary" href="/">🏠 Home Portal</a>
              </div>
            </div>
            """
            return render_page("Quiz Not Found", body, "Quiz Not Found"), 404

        is_rtl = has_arabic(quiz["question"])
        question = html.escape(quiz["question"])
        correct_idx = quiz["correct_option"]
        explanation = html.escape(quiz["explanation"])

        options_html = []
        for idx, opt in enumerate(quiz["options"]):
            label = chr(65 + idx)
            opt_text = html.escape(str(opt))
            options_html.append(
                f'<button class="option-btn" data-idx="{idx}" onclick="selectOption({idx})">'
                f'<span class="badge">{label}</span><span>{opt_text}</span></button>'
            )

        base_url = resolve_public_base_url(request.url_root)
        preview_url = f"{base_url}/quiz/{quiz_id}" if base_url else ""
        telegram_share = f"https://t.me/share/url?url={quote_plus(preview_url)}&text={quote_plus(quiz['question'][:120])}" if preview_url else ""
        wa_text = f"{quiz['question'][:100]}... {preview_url}" if preview_url else quiz['question']
        whatsapp_share = f"https://wa.me/?text={quote_plus(wa_text)}"
        x_share = f"https://twitter.com/intent/tweet?url={quote_plus(preview_url)}&text={quote_plus(quiz['question'][:120])}" if preview_url else ""
        deep_link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=quiz_{quiz_id}" if TELEGRAM_BOT_USERNAME else ""

        buttons = []
        if deep_link:
            buttons.append(f'<a class="btn btn-primary" href="{html.escape(deep_link)}">✈️ {"فتح في تيليجرام" if is_rtl else "Open in Telegram"}</a>')
        buttons.append(f'<button class="btn btn-outline" onclick="copyQuestion()">📋 {"نسخ السؤال" if is_rtl else "Copy"}</button>')
        if telegram_share:
            buttons.append(f'<a class="btn btn-outline" href="{html.escape(telegram_share)}">Telegram</a>')
        buttons.append(f'<a class="btn btn-outline" href="{html.escape(whatsapp_share)}">WhatsApp</a>')
        if x_share:
            buttons.append(f'<a class="btn btn-outline" href="{html.escape(x_share)}">X</a>')

        script = f"""
        <script>
          const correctIdx = {correct_idx};
          let answered = false;
          let soundEnabled = true;

          function toggleSound() {{
            soundEnabled = !soundEnabled;
            const btn = document.getElementById('soundToggleBtn');
            if (btn) btn.innerText = soundEnabled ? '🔊 Sound On' : '🔇 Muted';
          }}

          function playTone(freqs, type='sine', dur=0.18) {{
            if (!soundEnabled) return;
            try {{
              const AudioCtx = window.AudioContext || window.webkitAudioContext;
              if (!AudioCtx) return;
              const ctx = new AudioCtx();
              freqs.forEach((f, i) => {{
                const osc = ctx.createOscillator();
                const gain = ctx.createGain();
                osc.type = type;
                osc.frequency.setValueAtTime(f, ctx.currentTime + i * 0.07);
                gain.gain.setValueAtTime(0.15, ctx.currentTime + i * 0.07);
                gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + i * 0.07 + dur);
                osc.connect(gain);
                gain.connect(ctx.destination);
                osc.start(ctx.currentTime + i * 0.07);
                osc.stop(ctx.currentTime + i * 0.07 + dur);
              }});
            }} catch(e) {{}}
          }}

          function launchConfetti() {{
            const canvas = document.getElementById('confetti-canvas');
            if (!canvas) return;
            const ctx = canvas.getContext('2d');
            canvas.width = window.innerWidth;
            canvas.height = window.innerHeight;
            const pieces = [];
            const colors = ['#3b82f6', '#10b981', '#f59e0b', '#ec4899', '#8b5cf6'];
            for (let i = 0; i < 60; i++) {{
              pieces.push({{
                x: canvas.width / 2,
                y: canvas.height / 2,
                vx: (Math.random() - 0.5) * 14,
                vy: (Math.random() - 0.7) * 16,
                size: Math.random() * 8 + 4,
                color: colors[Math.floor(Math.random() * colors.length)],
                alpha: 1,
              }});
            }}
            let frame = 0;
            function draw() {{
              ctx.clearRect(0, 0, canvas.width, canvas.height);
              let active = false;
              pieces.forEach(p => {{
                p.x += p.vx;
                p.y += p.vy;
                p.vy += 0.5;
                p.alpha -= 0.015;
                if (p.alpha > 0) {{
                  active = true;
                  ctx.fillStyle = p.color;
                  ctx.globalAlpha = p.alpha;
                  ctx.fillRect(p.x, p.y, p.size, p.size);
                }}
              }});
              frame++;
              if (active && frame < 75) requestAnimationFrame(draw);
              else ctx.clearRect(0, 0, canvas.width, canvas.height);
            }}
            draw();
          }}

          function selectOption(chosenIdx) {{
            if (answered) return;
            answered = true;
            const buttons = document.querySelectorAll('.option-btn');
            buttons.forEach((btn, idx) => {{
              btn.disabled = true;
              if (idx === correctIdx) {{
                btn.classList.add('selected-correct');
              }} else if (idx === chosenIdx) {{
                btn.classList.add('selected-wrong');
              }}
            }});
            if (chosenIdx === correctIdx) {{
              playTone([523.25, 659.25, 783.99], 'sine', 0.22);
              launchConfetti();
            }} else {{
              playTone([311.13, 293.66], 'triangle', 0.26);
            }}
            const expBox = document.getElementById('explanationBox');
            if (expBox) expBox.classList.add('show');
          }}

          function copyQuestion() {{
            const q = document.getElementById('questionText').innerText;
            navigator.clipboard.writeText(q).then(() => {{
              const toast = document.getElementById('toast');
              if (toast) {{
                toast.classList.add('show');
                setTimeout(() => toast.classList.remove('show'), 2200);
              }}
            }});
          }}
        </script>
        """

        explanation_section = ""
        if explanation:
            explanation_section = f'<div id="explanationBox" class="explain-box"><strong>{"💡 التفسير التعليمي:" if is_rtl else "💡 Explanation:"}</strong><p style="margin-top: 6px;">{explanation}</p></div>'

        body = f"""
        <div class="card">
          <div class="top-meta">
            <span class="eyebrow">{"🎯 اختبار تفاعلي" if is_rtl else "🎯 Interactive Quiz"}</span>
            <button id="soundToggleBtn" class="sound-btn" onclick="toggleSound()">🔊 Sound On</button>
          </div>
          <h1 id="questionText">{question}</h1>
          <div class="options">{''.join(options_html)}</div>
          {explanation_section}
          <div class="actions">{''.join(buttons)}</div>
        </div>
        {script}
        """
        description = " | ".join(str(opt) for opt in quiz["options"][:4])
        return render_page(quiz["question"], body, description, is_rtl=is_rtl)


def run_web():
    """Run Flask development server."""
    if app is None:
        logger.warning("Flask is not installed. Web preview server is disabled.")
        return
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


def keep_alive():
    """Start web preview server in background daemon thread."""
    if app is None:
        logger.info("Web preview disabled: Flask is not installed.")
        return
    thread = Thread(target=run_web, daemon=True)
    thread.start()
    logger.info("Keep-alive & Web preview thread started on port %s", PORT)

import html
import json
import os
import sqlite3
from threading import Thread
from urllib.parse import quote_plus

from flask import Flask


DB_PATH = os.getenv("DB_PATH", "stats.db")
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
TELEGRAM_BOT_USERNAME = os.getenv("TELEGRAM_BOT_USERNAME", "").strip().lstrip("@")
PORT = int(os.getenv("PORT", "8080"))

app = Flask(__name__)


def fetch_quiz(quiz_id: str):
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
    finally:
        conn.close()


def render_page(title: str, body: str, description: str = "") -> str:
    safe_title = html.escape(title)
    safe_description = html.escape(description or title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <meta name="description" content="{safe_description}">
  <meta property="og:title" content="{safe_title}">
  <meta property="og:description" content="{safe_description}">
  <meta property="og:type" content="website">
  <meta property="twitter:card" content="summary_large_image">
  <style>
    :root {{
      --bg: #f5f0e6;
      --panel: #fffdf9;
      --line: #d8ccb8;
      --text: #2f2419;
      --muted: #75624f;
      --accent: #0f9d58;
      --accent-2: #b34700;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Segoe UI", Tahoma, sans-serif;
      background:
        radial-gradient(circle at top left, rgba(179, 71, 0, 0.16), transparent 28%),
        linear-gradient(180deg, #f9f4ea 0%, var(--bg) 100%);
      color: var(--text);
      min-height: 100vh;
    }}
    .wrap {{
      max-width: 760px;
      margin: 0 auto;
      padding: 32px 20px 48px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: 0 20px 60px rgba(47, 36, 25, 0.12);
      overflow: hidden;
    }}
    .head {{
      padding: 24px 24px 12px;
      border-bottom: 1px solid rgba(216, 204, 184, 0.6);
    }}
    .eyebrow {{
      display: inline-block;
      padding: 6px 10px;
      border-radius: 999px;
      background: rgba(15, 157, 88, 0.12);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.04em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 14px 0 8px;
      font-size: clamp(24px, 4vw, 36px);
      line-height: 1.2;
    }}
    .body {{ padding: 22px 24px 24px; }}
    .muted {{ color: var(--muted); }}
    .options {{
      display: grid;
      gap: 12px;
      margin-top: 22px;
    }}
    .option {{
      display: flex;
      gap: 14px;
      align-items: center;
      padding: 16px 18px;
      border-radius: 18px;
      border: 1px solid rgba(216, 204, 184, 0.9);
      background: #fff;
    }}
    .option.correct {{
      border-color: rgba(15, 157, 88, 0.45);
      background: rgba(15, 157, 88, 0.08);
    }}
    .badge {{
      width: 36px;
      height: 36px;
      border-radius: 50%;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-weight: 700;
      background: rgba(179, 71, 0, 0.12);
      color: var(--accent-2);
      flex: 0 0 auto;
    }}
    .option.correct .badge {{
      background: rgba(15, 157, 88, 0.16);
      color: var(--accent);
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 24px;
    }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 12px 16px;
      border-radius: 999px;
      text-decoration: none;
      color: white;
      background: linear-gradient(135deg, var(--accent-2), #d66700);
      font-weight: 600;
    }}
    .button.alt {{
      background: linear-gradient(135deg, #1275d1, #0f4d91);
    }}
    .button.ghost {{
      background: white;
      color: var(--text);
      border: 1px solid var(--line);
    }}
    .explain {{
      margin-top: 20px;
      padding: 16px 18px;
      border-radius: 18px;
      background: rgba(15, 157, 88, 0.06);
      border: 1px dashed rgba(15, 157, 88, 0.35);
    }}
  </style>
</head>
<body>
  <div class="wrap">{body}</div>
</body>
</html>"""


@app.route("/")
def home():
    body = """
    <div class="card">
      <div class="head">
        <span class="eyebrow">MCQ Bot</span>
        <h1>Quiz preview service is online</h1>
      </div>
      <div class="body">
        <p class="muted">Share links generated by the bot can open quiz preview pages here.</p>
      </div>
    </div>
    """
    return render_page("MCQ Bot", body, "Quiz preview service is online")


@app.route("/quiz/<quiz_id>")
def quiz_preview(quiz_id: str):
    quiz = fetch_quiz(quiz_id)
    if quiz is None:
        body = """
        <div class="card">
          <div class="head">
            <span class="eyebrow">MCQ Bot</span>
            <h1>Quiz not found</h1>
          </div>
          <div class="body">
            <p class="muted">This quiz may have expired or the link is invalid.</p>
          </div>
        </div>
        """
        return render_page("Quiz not found", body, "This quiz may have expired or the link is invalid."), 404

    question = html.escape(quiz["question"])
    options_html = []
    for idx, option in enumerate(quiz["options"]):
        label = chr(65 + idx)
        option_text = html.escape(str(option))
        css_class = "option correct" if idx == quiz["correct_option"] else "option"
        options_html.append(
            f'<div class="{css_class}"><span class="badge">{label}</span><div>{option_text}</div></div>'
        )

    preview_url = f"{PUBLIC_BASE_URL}/quiz/{quiz_id}" if PUBLIC_BASE_URL else ""
    share_seed = preview_url or quiz["question"]
    telegram_share = f"https://t.me/share/url?url={quote_plus(preview_url)}&text={quote_plus(quiz['question'][:120])}" if preview_url else ""
    whatsapp_share = f"https://wa.me/?text={quote_plus(share_seed)}"
    x_share = f"https://twitter.com/intent/tweet?url={quote_plus(preview_url)}&text={quote_plus(quiz['question'][:120])}" if preview_url else ""
    deep_link = f"https://t.me/{TELEGRAM_BOT_USERNAME}?start=quiz_{quiz_id}" if TELEGRAM_BOT_USERNAME else ""

    buttons = []
    if deep_link:
        buttons.append(f'<a class="button alt" href="{html.escape(deep_link)}">Open in Telegram</a>')
    if telegram_share:
        buttons.append(f'<a class="button" href="{html.escape(telegram_share)}">Share on Telegram</a>')
    buttons.append(f'<a class="button ghost" href="{html.escape(whatsapp_share)}">WhatsApp</a>')
    if x_share:
        buttons.append(f'<a class="button ghost" href="{html.escape(x_share)}">X</a>')

    explanation_html = ""
    if quiz["explanation"]:
        explanation_html = f'<div class="explain"><strong>Explanation</strong><div>{html.escape(quiz["explanation"])}</div></div>'

    body = f"""
    <div class="card">
      <div class="head">
        <span class="eyebrow">Quiz Preview</span>
        <h1>{question}</h1>
        <div class="muted">Shared from MCQ Bot</div>
      </div>
      <div class="body">
        <div class="options">{''.join(options_html)}</div>
        {explanation_html}
        <div class="actions">{''.join(buttons)}</div>
      </div>
    </div>
    """
    description = " | ".join(str(option) for option in quiz["options"][:4])
    return render_page(quiz["question"], body, description)


def run():
    app.run(host="0.0.0.0", port=PORT, threaded=True, use_reloader=False)


def keep_alive():
    thread = Thread(target=run, daemon=True)
    thread.start()

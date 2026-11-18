"""
Interactive Exam / Test Session service for private chats and groups.
Allows students to take timed quizzes with instant scoring and performance reports.
"""

from dataclasses import dataclass, field
import time
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class ExamSession:
    user_id: int
    chat_id: int
    quizzes: List[Dict[str, Any]]
    current_index: int = 0
    correct_count: int = 0
    start_time: float = field(default_factory=time.time)
    lang: str = "ar"
    is_active: bool = True


# In-memory storage of active exams: (user_id, chat_id) -> ExamSession
_active_exams: Dict[Tuple[int, int], ExamSession] = {}


def start_exam(user_id: int, chat_id: int, quizzes: List[Dict[str, Any]], lang: str = "ar") -> Optional[ExamSession]:
    """Start a new exam session for user in chat."""
    if not quizzes:
        return None
    session = ExamSession(
        user_id=user_id,
        chat_id=chat_id,
        quizzes=quizzes,
        current_index=0,
        correct_count=0,
        start_time=time.time(),
        lang=lang,
        is_active=True,
    )
    _active_exams[(user_id, chat_id)] = session
    return session


def get_exam(user_id: int, chat_id: int) -> Optional[ExamSession]:
    """Get currently active exam session."""
    session = _active_exams.get((user_id, chat_id))
    if session and session.is_active:
        return session
    return None


def finish_exam(user_id: int, chat_id: int) -> Optional[Dict[str, Any]]:
    """Finish the session and calculate final score and summary."""
    session = _active_exams.pop((user_id, chat_id), None)
    if not session:
        return None
    
    session.is_active = False
    total = len(session.quizzes)
    correct = session.correct_count
    wrong = total - correct
    score_pct = round((correct / total) * 100) if total > 0 else 0
    duration_secs = int(time.time() - session.start_time)
    
    if session.lang == "ar":
        if score_pct >= 90:
            feedback = "🌟 ممتاز جداً! أداء استثنائي وإتقان تام للمادة!"
        elif score_pct >= 75:
            feedback = "👏 رائع جداً! مستوى متقدم ولديك فهم قوي للموضوع."
        elif score_pct >= 50:
            feedback = "👍 جيد! اجتزت الاختبار بنجاح، وبعض المراجعة سترفع مستواك للأفضل."
        else:
            feedback = "💪 بداية جيدة! راجع الملخص وحاول مرة أخرى، التكرار سر الإتقان."
    else:
        if score_pct >= 90:
            feedback = "🌟 Outstanding! Exceptional performance and mastery!"
        elif score_pct >= 75:
            feedback = "👏 Great job! Advanced comprehension and strong retention."
        elif score_pct >= 50:
            feedback = "👍 Good! You passed, and reviewing key points will boost your score."
        else:
            feedback = "💪 Keep practicing! Review the material and try again."

    return {
        "total": total,
        "correct": correct,
        "wrong": wrong,
        "score": score_pct,
        "duration": duration_secs,
        "feedback": feedback,
        "lang": session.lang,
    }

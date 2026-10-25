from datetime import date

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def fmt_due(due: str) -> str:
    d = date.fromisoformat(due)
    return f"до {WEEKDAYS[d.weekday()]}, {d:%d.%m}"


def render_tasks(tasks) -> str:
    """tasks — строки из get_tasks. Делает из них текст сводки."""
    lessons = [t for t in tasks if t[1] == "lesson"]
    homework = [t for t in tasks if t[1] == "homework"]

    parts = []
    if lessons:
        lines = ["📚 Что проходили"]
        lines += [f"• {subject}: {text}" for _id, _kind, subject, text, _due, _ts in lessons]
        parts.append("\n".join(lines))
        
    if homework:
        lines = ["📝 Домашнее задание и дедлайны"]
        for _id, _kind, subject, text, due, _ts in homework:
            tail = f" — {fmt_due(due)}" if due else ""
            lines.append(f"• {subject}: {text}{tail}")
        parts.append("\n".join(lines))

    if not parts:
        return "Пока ничего не нашёл: ни домашки, ни тем уроков."
    return "\n\n".join(parts) + "\n\nИИ · не проверено учителем"
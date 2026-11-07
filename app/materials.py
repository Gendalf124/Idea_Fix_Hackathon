"""Материалы по предметам: файлы и фото из чата, разложенные по предметам.

Бот не скачивает файлы. Он запоминает название, предмет, дату и токен-ссылку MAX, по которой файл
можно прислать обратно. Предмет определяется без ИИ: по подписи, имени файла и соседним сообщениям.
"""
import logging
import re
from datetime import datetime

from maxapi.types import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder

from app.tasks_db import TZ

MAX_BUTTONS = 16

SUBJECTS = [  # порядок важен: «алгебра» раньше «математика»
    ("Алгебра", r"алгебр"),
    ("Геометрия", r"геометр"),
    ("Математика", r"математик|матем\b|матан"),
    ("Русский язык", r"русск|рус\.?\s?яз"),
    ("Литература", r"литератур|лит-ра|\bлит\b"),
    ("Физика", r"физик"),
    ("Химия", r"хими"),
    ("Биология", r"биолог"),
    ("География", r"географ"),
    ("История", r"истори"),
    ("Обществознание", r"обществ"),
    ("Информатика", r"информатик"),
    ("Английский язык", r"англ"),
    ("Немецкий язык", r"немецк"),
    ("Башкирский язык", r"башк"),
    ("Татарский язык", r"татарск"),
    ("Технология", r"технолог|\bтруд\b"),
    ("ОБЖ", r"\bобж\b|\bобзр\b"),
    ("Физкультура", r"физкульт|физра"),
    ("Музыка", r"музык"),
    ("ИЗО", r"\bизо\b|рисован"),
]
_SUBJECT_RE = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in SUBJECTS]

# Фото без подписи про учёбу материалом не считаем (там могут быть люди, мемы, пара кадров с перемены).
MATERIAL_WORDS = re.compile(
    r"конспект|учебник|презентац|шпаргалк|материал|лекци|таблиц|схем|памятк|образец|пример|шаблон|разбор|"
    r"справочник|формул|правил|словар",
    re.IGNORECASE,
)

NO_SUBJECT = "Без предмета"


def guess_subject(*texts) -> str:
    """Предмет по первому тексту, где он назван. Тексты: подпись, имя файла, соседние сообщения."""
    for text in texts:
        if not text:
            continue
        for name, rx in _SUBJECT_RE:
            if rx.search(text):
                return name
    return NO_SUBJECT


def is_material_text(text: str) -> bool:
    return bool(text) and bool(MATERIAL_WORDS.search(text))


def _enum_str(value) -> str:
    return str(getattr(value, "value", value)).lower()


def file_infos(message):
    """Файлы (документы) из вложений: [{"title", "token"}]. Поля читаем осторожно."""
    body = getattr(message, "body", None)
    result = []
    for att in getattr(body, "attachments", None) or []:
        if _enum_str(getattr(att, "type", "")) != "file":
            continue
        payload = getattr(att, "payload", None)
        token = getattr(payload, "token", None) or getattr(att, "token", None)
        title = getattr(att, "filename", None) or getattr(payload, "filename", None) or "Файл"
        result.append({"title": str(title), "token": token})
    return result


def attachment_by_token(kind: str, token):
    """Вложение «файл по токену» для отправки через maxapi. Если собрать не удалось — None.

    Классы в разных версиях maxapi называются по-разному, поэтому пробуем несколько вариантов."""
    if not token:
        return None
    if kind == "photo":
        from app.photos import photo_attachment
        return photo_attachment(token)
    from app.photos import upload_attachment
    ready = upload_attachment("file", token)
    if ready is not None:
        return ready
    attempts = (
        ("maxapi.types.attachments.file", "FileAttachmentRequest", "FileAttachmentRequestPayload"),
        ("maxapi.types.attachments.attachment", "FileAttachmentRequest", "FileAttachmentRequestPayload"),
        ("maxapi.types", "FileAttachmentRequest", "FileAttachmentRequestPayload"),
        ("maxapi.types.attachments.file", "FileAttachmentRequest", "OtherAttachmentPayload"),
        ("maxapi.types", "AttachmentRequest", "OtherAttachmentPayload"),
    )
    for module_name, req_name, payload_name in attempts:
        try:
            module = __import__(module_name, fromlist=[req_name, payload_name])
            return getattr(module, req_name)(payload=getattr(module, payload_name)(token=token))
        except Exception:
            continue
    logging.info("Материалы: не удалось собрать вложение-файл по токену (версия maxapi)")
    return None


def save_from_message(chat_id: int, mid: str, ts: int, text: str, files, images, context_texts=()):
    """Сохраняет файлы и «учебные» фото сообщения. Возвращает сколько материалов добавлено.

    Файл сохраняется всегда. Фото — только если подпись или имя говорят, что это материал."""
    from app.extras_db import add_material

    added = 0
    for i, info in enumerate(files):
        subject = guess_subject(text, info["title"], *context_texts)
        if add_material(chat_id, f"{mid}|f{i}|{info.get('token') or info['title']}", subject,
                        info["title"], "file", info.get("token"), ts):
            added += 1
    if images and is_material_text(text):
        subject = guess_subject(text, *context_texts)
        for i, info in enumerate(images):
            if add_material(chat_id, f"{mid}|p{i}|{info.get('token') or i}", subject, "Фото", "photo",
                            info.get("token"), ts):
                added += 1
    return added


def save_homework_photos(chat_id: int, mid: str, ts: int, subject: str, tokens, fallback_texts=()):
    """Фото, по которому составлена запись о ДЗ, тоже попадает в материалы своего предмета."""
    from app.extras_db import add_material

    if not subject or subject == NO_SUBJECT:
        subject = guess_subject(*fallback_texts)
    added = 0
    for i, token in enumerate(tokens):
        if token and add_material(chat_id, f"{mid}|h{i}|{token}", subject, "Фото задания", "photo", token, ts):
            added += 1
    return added


# ---------- показ ----------

def render_materials(rows):
    """Текст списка и порядок материалов (как они пронумерованы). rows — из get_materials."""
    if not rows:
        return "Материалов пока нет.\nПришлите в чат файл или фото с подписью («конспект по физике»), я разложу по предметам.", []
    parts, ordered, number, current = [], [], 0, None
    lines = []
    for row in rows:
        _id, subject, title, kind, ts = row
        if subject != current:
            if lines:
                parts.append("\n".join(lines))
            lines = [f"📘 {subject}"]
            current = subject
        number += 1
        ordered.append(row)
        icon = "📷" if kind == "photo" else "📄"
        lines.append(f"{number}. {icon} {title} · {datetime.fromtimestamp(ts / 1000, tz=TZ):%d.%m}")
    parts.append("\n".join(lines))
    text = "📎 Материалы класса\n\n" + "\n\n".join(parts) + "\n\n📎 1, 2… — прислать файл ещё раз"
    if len(ordered) > MAX_BUTTONS:
        text += f"\nПоказаны кнопки для первых {MAX_BUTTONS}."
    return text, ordered


def build_keyboard(ordered, extra=None):
    builder = InlineKeyboardBuilder()
    buttons = [
        CallbackButton(text=f"📎 {i}", payload=f"mf:{row[0]}")
        for i, row in enumerate(ordered[:MAX_BUTTONS], start=1)
    ]
    for i in range(0, len(buttons), 4):
        builder.row(*buttons[i : i + 4])
    if extra:
        builder.row(*extra)
    return builder.as_markup()
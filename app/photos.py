"""Фото: найти во вложениях, скачать в память, отправить обратно по токену MAX.

Сами картинки мы не храним: в записи остаётся только токен — ссылка на файл на серверах MAX.
"""
import logging
import os
from urllib.parse import urlparse

MAX_BYTES = 10 * 1024 * 1024
MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
        ".gif": "image/gif", ".bmp": "image/bmp", ".heic": "image/heic", ".tiff": "image/tiff"}


def _enum_str(value) -> str:
    return str(getattr(value, "value", value)).lower()


def image_infos(message):
    """Фото из вложений сообщения: список {"url": ..., "token": ...}. Поля читаем осторожно."""
    body = getattr(message, "body", None)
    result = []
    for att in getattr(body, "attachments", None) or []:
        if _enum_str(getattr(att, "type", "")) != "image":
            continue
        payload = getattr(att, "payload", None)
        url = getattr(payload, "url", None)
        token = getattr(payload, "token", None)
        if url or token:
            result.append({"url": url, "token": token})
    return result


def shrink(data: bytes, mime: str, max_side: int = 1280, quality: int = 80):
    """Уменьшает фото перед отправкой ИИ: снимок с телефона 3–8 МБ превращается в ~150–300 КБ.

    Так запрос уходит и читается в разы быстрее, а текст на доске/в тетради остаётся разборчивым.
    Если Pillow не установлен или формат не открылся, возвращаем фото как есть.
    """
    try:
        import io

        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)  # поворот как на телефоне
            img.thumbnail((max_side, max_side))
            if img.mode not in ("RGB", "L"):
                img = img.convert("RGB")
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
        small = out.getvalue()
        return (small, "image/jpeg") if len(small) < len(data) else (data, mime)
    except Exception as exc:
        logging.info("Фото не уменьшено (%s), отправляем как есть", exc)
        return data, mime


async def download_image(url: str):
    """Скачивает фото в память. Возвращает (байты, mime-тип)."""
    if not url or not url.startswith("https://"):
        raise ValueError("ссылка на фото не https")
    import httpx  # приходит вместе с openai

    ext = os.path.splitext(urlparse(url).path)[1].lower()
    mime = MIME.get(ext, "image/jpeg")
    buf = bytearray()
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as http:
        async with http.stream("GET", url) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > MAX_BYTES:
                    raise ValueError("фото слишком большое")
    data, mime = shrink(bytes(buf), mime)
    logging.info("Фото готово к отправке ИИ: %d КБ", len(data) // 1024)
    return data, mime


def upload_attachment(kind: str, token):
    """Вложение «по токену» через AttachmentUpload (так устроено в части версий maxapi).
    kind: "image" или "file". Если классов нет — None."""
    try:
        from maxapi.enums.upload_type import UploadType
        from maxapi.types.attachments.upload import AttachmentPayload, AttachmentUpload

        upload_type = getattr(UploadType, kind.upper())
        return AttachmentUpload(type=upload_type, payload=AttachmentPayload(token=token))
    except Exception:
        return None


def photo_attachment(token):
    """Вложение «фото по токену» для отправки через maxapi. Если собрать не удалось — None.

    Названия классов зависят от версии maxapi, поэтому пробуем несколько вариантов.
    """
    if not token:
        return None
    ready = upload_attachment("image", token)
    if ready is not None:
        return ready
    attempts = (
        ("maxapi.types.attachments.image", "PhotoAttachmentRequestPayload"),
        ("maxapi.types.attachments.attachment", "PhotoAttachmentRequestPayload"),
        ("maxapi.types", "PhotoAttachmentRequestPayload"),
    )
    for module_name, class_name in attempts:
        try:
            module = __import__(module_name, fromlist=[class_name])
            payload_cls = getattr(module, class_name)
            for req_module, req_name in (
                ("maxapi.types.attachments.image", "PhotoAttachmentRequest"),
                ("maxapi.types.attachments.attachment", "PhotoAttachmentRequest"),
                ("maxapi.types", "PhotoAttachmentRequest"),
            ):
                try:
                    req_cls = getattr(__import__(req_module, fromlist=[req_name]), req_name)
                    return req_cls(payload=payload_cls(token=token))
                except Exception:
                    continue
        except Exception:
            continue
    logging.info("Фото: не удалось собрать вложение по токену (версия maxapi)")
    return None
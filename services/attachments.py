"""
Turning an incoming Telegram message into a homework attachment *reference*.

The bot never downloads, stores or opens a binary. It keeps only what Telegram
gives it for free — ``file_id`` / ``file_unique_id`` plus size, kind and the
sanitised original name — and hands the ``file_id`` back to Telegram when the
card is shown. Consequences worth being explicit about:

  * nothing is written to disk, so there is no path traversal surface and the
    client-supplied file name is display metadata only (sanitised anyway, see
    utils.safe_file_name);
  * nothing is unpacked or executed — an archive or a ``.exe`` is just an opaque
    reference we can forward back, never something we act on;
  * validation (kind / size / caption length) happens on the metadata Telegram
    reports, which is all we need to refuse something we could not re-send.

Two accepted kinds:

  * ``photo``    — a compressed photo (we take the largest ``PhotoSize``);
  * ``document`` — anything sent as a file, *including an image sent as a file*.
    That distinction is preserved on purpose: a photo sent as a document keeps
    its original quality, exactly as the sender intended.

Anything else (video, audio, voice, sticker, animation…) is refused with a clear
message rather than being silently dropped or half-stored.
"""
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from utils import (
    MAX_ATTACHMENT_CAPTION_LEN, MAX_ATTACHMENT_SIZE_BYTES, format_file_size,
    safe_file_name,
)

# Human labels for the message kinds we deliberately refuse. Keys are the
# aiogram ``Message`` attribute names checked in :func:`extract_attachment`.
UNSUPPORTED_KINDS = {
    "video": "видео",
    "video_note": "видеосообщение",
    "animation": "GIF-анимацию",
    "audio": "аудиофайл",
    "voice": "голосовое сообщение",
    "sticker": "стикер",
    "contact": "контакт",
    "location": "геолокацию",
    "poll": "опрос",
    "dice": "кубик",
}

UNSUPPORTED_TEXT = (
    "🤔 Пока можно приложить только фотографию или файл (документ). "
    "Пришли фото или отправь изображение как файл."
)

NOTHING_ATTACHED_TEXT = (
    "🤔 Здесь нужно прислать фотографию или файл. "
    "Если вложения не нужны — нажми «✅ Готово»."
)

TOO_BIG_TEXT = (
    "⚠️ Файл слишком большой ({size}). Telegram не даст переслать его обратно — "
    "максимум {limit}. Пришли файл поменьше."
)


@dataclass
class AttachmentInfo:
    """A validated, ready-to-store attachment reference (no binary data)."""
    file_id: str
    file_unique_id: str
    file_type: str            # "photo" | "document"
    file_name: Optional[str]  # sanitised original name, display only
    file_size: Optional[int]  # bytes, as reported by Telegram
    caption: Optional[str]    # optional, already length-capped


def _clean_caption(raw: Optional[str]) -> Optional[str]:
    """
    Normalise a caption: trim, collapse blank runs, cut to the stored limit.

    Over-long captions are truncated rather than rejected — the user attached
    the right file, and refusing it over a chatty caption would be needlessly
    hostile. The visible ellipsis makes the truncation obvious.
    """
    if not raw:
        return None
    text = "\n".join(line.strip() for line in str(raw).splitlines() if line.strip())
    if not text:
        return None
    if len(text) > MAX_ATTACHMENT_CAPTION_LEN:
        text = text[: MAX_ATTACHMENT_CAPTION_LEN - 1].rstrip() + "…"
    return text


def _photo_from(message: Any) -> Optional[Any]:
    """The largest ``PhotoSize`` of a photo message, or None."""
    photos = getattr(message, "photo", None)
    if not photos:
        return None
    # Telegram sends ascending sizes; be explicit rather than relying on order.
    return max(photos, key=lambda p: (getattr(p, "file_size", 0) or 0, getattr(p, "width", 0) or 0))


def extract_attachment(message: Any) -> Tuple[Optional[AttachmentInfo], Optional[str]]:
    """
    ``(attachment, error_text)`` for one incoming message — exactly one is set.

    ``error_text`` is a ready-to-send, user-facing explanation, so a handler can
    simply forward it and stay in the same FSM step for a retry.
    """
    caption = _clean_caption(getattr(message, "caption", None))

    photo = _photo_from(message)
    if photo is not None:
        size = getattr(photo, "file_size", None)
        if size and size > MAX_ATTACHMENT_SIZE_BYTES:
            return None, TOO_BIG_TEXT.format(
                size=format_file_size(size),
                limit=format_file_size(MAX_ATTACHMENT_SIZE_BYTES),
            )
        return AttachmentInfo(
            file_id=photo.file_id,
            file_unique_id=photo.file_unique_id,
            file_type="photo",
            file_name=None,  # compressed photos have no meaningful name
            file_size=size,
            caption=caption,
        ), None

    document = getattr(message, "document", None)
    if document is not None:
        size = getattr(document, "file_size", None)
        if size and size > MAX_ATTACHMENT_SIZE_BYTES:
            return None, TOO_BIG_TEXT.format(
                size=format_file_size(size),
                limit=format_file_size(MAX_ATTACHMENT_SIZE_BYTES),
            )
        return AttachmentInfo(
            file_id=document.file_id,
            file_unique_id=document.file_unique_id,
            file_type="document",
            # Untrusted client value → sanitised; never a path, never opened.
            file_name=safe_file_name(getattr(document, "file_name", None)),
            file_size=size,
            caption=caption,
        ), None

    for attribute in UNSUPPORTED_KINDS:
        if getattr(message, attribute, None) is not None:
            return None, UNSUPPORTED_TEXT

    return None, NOTHING_ATTACHED_TEXT

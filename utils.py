from aiogram.exceptions import TelegramBadRequest


def escape_markdown(text: str) -> str:
    """
    Escapes special Markdown V1 characters in user-provided text
    to prevent Telegram parser crashes.
    Characters escaped: * _ ` [
    """
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text


async def safe_edit_text(message, text, **kwargs):
    """
    Edits a message's text while ignoring Telegram's "message is not modified"
    error, which is raised when the new content is identical to the current one
    (e.g. tapping a refresh button or re-selecting the already active day).
    """
    try:
        await message.edit_text(text, **kwargs)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            raise

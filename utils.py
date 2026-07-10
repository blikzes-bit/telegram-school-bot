def escape_markdown(text: str) -> str:
    """
    Escapes special Markdown V1 characters in user-provided text
    to prevent Telegram parser crashes.
    Characters escaped: * _ ` [
    """
    for ch in ("*", "_", "`", "["):
        text = text.replace(ch, f"\\{ch}")
    return text

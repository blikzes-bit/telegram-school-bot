"""Application layer: DTOs and query use-cases shared by the bot and the web API.

Nothing here imports ``handlers/`` (the Telegram adapter) or ``web_api/`` (the
HTTP adapter); both adapters depend inward on this layer, which in turn depends
only on ``services/`` and ``database/``.
"""

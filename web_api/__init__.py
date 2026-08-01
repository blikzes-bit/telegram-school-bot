"""FastAPI web backend for the Telegram Mini App (stage 1, read-only).

The API depends inward on ``application/`` (DTOs + query use-cases) and
``services/`` + ``database/``. It must never import anything from ``handlers/``
(the Telegram adapter): the two adapters share domain logic only through the
application/service layers.
"""

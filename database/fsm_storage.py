"""
A minimal SQLite-backed FSM storage so onboarding/edit progress survives a
bot restart or crash, without adding an external dependency (e.g. Redis) for
a project that is otherwise fully self-contained on SQLite.

Selected via ``FSM_STORAGE=sqlite`` (the default — see config.py). Set
``FSM_STORAGE=memory`` for local development if persistence across restarts
is not desired.
"""
import json
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from sqlalchemy import select, update

from database.models import FSMStateRow
from database.db import AsyncSessionLocal


def _key(storage_key: StorageKey) -> str:
    return f"{storage_key.bot_id}:{storage_key.chat_id}:{storage_key.user_id}"


class SQLAlchemyStorage(BaseStorage):
    async def set_state(self, key: StorageKey, state: Optional[Any] = None) -> None:
        state_str = state.state if isinstance(state, State) else state
        row_key = _key(key)
        async with AsyncSessionLocal() as session:
            existing = await session.execute(select(FSMStateRow).where(FSMStateRow.key == row_key))
            row = existing.scalar_one_or_none()
            if row is None:
                session.add(FSMStateRow(key=row_key, state=state_str, data="{}"))
            else:
                await session.execute(
                    update(FSMStateRow).where(FSMStateRow.key == row_key).values(state=state_str)
                )
            await session.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        row_key = _key(key)
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(FSMStateRow).where(FSMStateRow.key == row_key))
            row = result.scalar_one_or_none()
            return row.state if row is not None else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        row_key = _key(key)
        payload = json.dumps(data)
        async with AsyncSessionLocal() as session:
            existing = await session.execute(select(FSMStateRow).where(FSMStateRow.key == row_key))
            row = existing.scalar_one_or_none()
            if row is None:
                session.add(FSMStateRow(key=row_key, state=None, data=payload))
            else:
                await session.execute(
                    update(FSMStateRow).where(FSMStateRow.key == row_key).values(data=payload)
                )
            await session.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        row_key = _key(key)
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(FSMStateRow).where(FSMStateRow.key == row_key))
            row = result.scalar_one_or_none()
            if row is None or not row.data:
                return {}
            return json.loads(row.data)

    async def close(self) -> None:
        pass

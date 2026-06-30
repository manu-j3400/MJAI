"""
UnamOS Persistent Memory Store
MongoDB-backed key-value memory that persists across sessions.
Workflows read/write memory via {{memory.key}} template variables
and memory_set / memory_get / memory_append actions.
"""
import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "unamosvos"
COLLECTION = "memory"


class MemoryStore:
    def __init__(self):
        self._db = None
        self._col = None
        self._cache: dict[str, Any] = {}

    async def connect(self) -> bool:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=2000)
            await client.admin.command("ping")
            self._db = client[DB_NAME]
            self._col = self._db[COLLECTION]
            await self._warm_cache()
            log.info("Memory store connected (MongoDB %s/%s)", DB_NAME, COLLECTION)
            return True
        except Exception as e:
            log.warning("Memory store offline (MongoDB unavailable): %s", e)
            return False

    async def _warm_cache(self):
        async for doc in self._col.find({}, {"_id": 0}):
            self._cache[doc["key"]] = doc["value"]
        log.info("Memory cache warmed: %d keys", len(self._cache))

    async def get(self, key: str, default: Any = None) -> Any:
        return self._cache.get(key, default)

    async def set(self, key: str, value: Any) -> None:
        self._cache[key] = value
        if self._col is not None:
            await self._col.update_one(
                {"key": key},
                {"$set": {"key": key, "value": value, "updated_at": datetime.now(timezone.utc).isoformat()}},
                upsert=True,
            )

    async def append(self, key: str, value: Any, max_len: int = 100) -> None:
        existing = self._cache.get(key, [])
        if not isinstance(existing, list):
            existing = [existing]
        existing.append(value)
        if len(existing) > max_len:
            existing = existing[-max_len:]
        await self.set(key, existing)

    async def delete(self, key: str) -> None:
        self._cache.pop(key, None)
        if self._col is not None:
            await self._col.delete_one({"key": key})

    async def all(self) -> dict:
        return dict(self._cache)

    def as_context(self) -> dict:
        """Return memory as flat {{memory.key}} template variables."""
        return {f"memory.{k}": v for k, v in self._cache.items()}


# Singleton
_store: Optional[MemoryStore] = None


def get_store() -> MemoryStore:
    global _store
    if _store is None:
        _store = MemoryStore()
    return _store

"""Minimal Shinigami response contracts."""
from pydantic import BaseModel, ConfigDict


class ShinigamiManga(BaseModel):
    model_config = ConfigDict(extra="allow")
    manga_id: str | int
    latest_chapter_time: str | None = None


class ShinigamiLatestResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    data: list[ShinigamiManga]


class ShinigamiDetailResponse(BaseModel):
    model_config = ConfigDict(extra="allow")
    data: dict

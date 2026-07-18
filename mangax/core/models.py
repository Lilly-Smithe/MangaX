# Reader ve Full ortak API modelleri.
# API request/response modelleri

from typing import Literal, Optional
from pydantic import BaseModel, Field


class DownloadRequest(BaseModel):
    manga_id: str
    chapter_id: str
    chapter_num: str
    title: str
    language: str
    manga_title: str
    source_manga_id: Optional[str] = None
    compression_profile: Literal["quality", "balanced", "compact"] = "balanced"


class DownloadMoveRequest(BaseModel):
    direction: Literal["up", "down"]


class ProgressRequest(BaseModel):
    manga_id: str
    chapter_id: str
    page_index: int
    manga_title: str = ""
    description: str = ""
    cover_url: str = ""
    status: str = "ongoing"
    chapter_num: str = ""
    chapter_title: str = ""
    source_id: str = ""
    language: Literal["tr", "en"] = "tr"
    online: bool = True
    page_offset: float = 0.0
    chapter_percent: float = 0.0


class DeleteRequest(BaseModel):
    manga_id: str
    chapter_id: str


LibraryStatus = Literal["reading", "completed", "on_hold", "dropped"]


class LibraryMetadataRequest(BaseModel):
    library_status: LibraryStatus = "reading"
    user_rating: int = Field(default=0, ge=0, le=10)
    personal_note: str = Field(default="", max_length=4000)
    collections: list[str] = Field(default_factory=list, max_length=20)


class LibraryBulkUpdateRequest(BaseModel):
    manga_ids: list[str] = Field(min_length=1, max_length=200)
    library_status: Optional[LibraryStatus] = None
    add_collection: str = Field(default="", max_length=40)


class KnownChaptersRequest(BaseModel):
    chapter_numbers: list[str] = Field(default_factory=list, max_length=5000)


class TrackingPreferenceRequest(BaseModel):
    enabled: bool = False
    notifications: bool = True
    auto_download: bool = False


class ChapterTrackerSettingsRequest(BaseModel):
    enabled: bool = True
    interval_minutes: Literal[15, 30, 60, 180] = 30


class AddSourceRequest(BaseModel):
    key: str = ""
    url: str
    name: str
    theme: Optional[str] = "generic"
    language: Literal["tr", "en"] = "tr"


class ToggleSourceRequest(BaseModel):
    source_id: str
    enabled: bool

from pydantic import BaseModel
from typing import Optional, List, Union


class LoginRequest(BaseModel):
    username: str
    password: str


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    username: Optional[str] = None


class CodeRequest(BaseModel):
    code: str


class PasswordRequest(BaseModel):
    password: str


class DownloadRequest(BaseModel):
    channel_username: str
    limit: Optional[int] = 10
    filter_type: Optional[str] = None
    search_query: Optional[str] = None
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    file_extension: Optional[str] = None
    subfolder: Optional[str] = None
    organize_by: Optional[str] = None


class DownloadSelectedRequest(BaseModel):
    channel_username: str
    message_ids: List[int]
    subfolder: Optional[str] = None
    organize_by: Optional[str] = None


class QuickFetchRequest(BaseModel):
    links: Union[str, List[str]]
    subfolder: Optional[str] = None
    organize_by: Optional[str] = None


class WatchRuleCreateRequest(BaseModel):
    channel: str
    channel_name: Optional[str] = None
    keywords: Optional[str] = None
    file_extensions: Optional[str] = None
    min_size_mb: Optional[float] = None
    max_size_mb: Optional[float] = None
    subfolder: Optional[str] = None
    organize_by: Optional[str] = None
    enabled: bool = True


class WatchRuleUpdateRequest(BaseModel):
    channel: Optional[str] = None
    channel_name: Optional[str] = None
    keywords: Optional[str] = None
    file_extensions: Optional[str] = None
    min_size_mb: Optional[float] = None
    max_size_mb: Optional[float] = None
    subfolder: Optional[str] = None
    organize_by: Optional[str] = None
    enabled: Optional[bool] = None


class FileInfo(BaseModel):
    file_id: int
    file_name: str
    file_size: int
    file_type: str
    date: str

class QueueReorderRequest(BaseModel):
    queue_ids: List[str]

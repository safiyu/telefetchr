from pydantic import BaseModel
from typing import Optional, List


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


class DownloadSelectedRequest(BaseModel):
    channel_username: str
    message_ids: List[int]


class FileInfo(BaseModel):
    file_id: int
    file_name: str
    file_size: int
    file_type: str
    date: str

import os
import logging
from typing import Optional
from app.config import Config

logger = logging.getLogger(__name__)


def sanitize_subfolder(subfolder: Optional[str]) -> str:
    """Sanitize relative subfolder path, preventing path traversal."""
    if not subfolder:
        return ""
    cleaned = subfolder.strip().replace("\\", "/").strip("/")
    parts = []
    for part in cleaned.split("/"):
        part = part.strip()
        if not part or part == ".":
            continue
        if part == "..":
            raise ValueError("Directory traversal not allowed in subfolder")
        safe_part = "".join(c for c in part if c not in '<>:"|?*').strip()
        if safe_part:
            parts.append(safe_part)
    return os.path.join(*parts) if parts else ""


def resolve_destination_dir(
    subfolder: Optional[str] = None,
    organize_by: Optional[str] = None,
    channel_name: Optional[str] = None,
    media_type: Optional[str] = None,
    base_path: Optional[str] = None
) -> str:
    """
    Resolve target destination directory strictly within base_path (or Config.SAVE_PATH),
    incorporating optional subfolder and channel/type organization.
    """
    base_save = os.path.realpath(base_path or Config.SAVE_PATH)
    extra_parts = []

    clean_sub = sanitize_subfolder(subfolder)
    if clean_sub:
        extra_parts.append(clean_sub)

    if organize_by == "channel" and channel_name:
        safe_channel = sanitize_subfolder(channel_name)
        if safe_channel:
            extra_parts.append(safe_channel)
    elif organize_by in ("type", "media_type") and media_type:
        safe_type = sanitize_subfolder(media_type.lower())
        if safe_type:
            extra_parts.append(safe_type)

    if extra_parts:
        target = os.path.realpath(os.path.join(base_save, *extra_parts))
    else:
        target = base_save

    # Strict canonical path jail verification
    if not (target == base_save or target.startswith(base_save + os.sep)):
        raise ValueError(f"Path jail violation: {target} is outside {base_save}")

    os.makedirs(target, exist_ok=True)
    return target

import os
import re
import unicodedata
from datetime import datetime

# Characters allowed in file basenames (everything else is stripped).
_ALLOWED_CHARS = re.compile(r"[^a-zA-Z0-9._\-\ \(\)\[\]]")

# Template tokens for directory formatting.
_DIRECTORY_TOKENS = ("channel", "year", "month", "day", "type", "ext")


def tidy_display_name(text: str) -> str:
    """
    Clean and normalize display names:
    1. Normalize unicode (NFKC) to fix "weird fonts".
    2. Remove extra whitespace and trim.
    3. Fix inconsistent casing (shouty or all lower).
    """
    if not text:
        return ""

    # Normalize unicode to standard forms (handles fancy fonts/bold/italic chars)
    text = unicodedata.normalize('NFKC', text)

    # Remove extra spaces and trim
    text = " ".join(text.split())

    # Only fix casing if it's extreme and the name is reasonably long
    # We avoid fixing short acronyms (up to 4 chars like NASA, CCTV)
    if len(text) > 4:
        if text.isupper():
            text = text.title()
        elif text.islower():
            text = text.title()

    return text


def sanitize_filename(name: str, max_base_bytes: int = 240) -> str:
    """Normalize Unicode (NFKC), strip traversal sequences, sanitize characters,
    and enforce byte-length limits preserving file extension.

    Guards filesystems against ENAMETOOLONG, path traversal and malicious
    character sequences in Telegram document filenames.
    """
    if not name:
        return "unnamed_file"

    # Normalize unicode to a canonical form (handles fancy/bidi/compat chars)
    name = unicodedata.normalize("NFKC", name)

    # Strip any directory components / traversal sequences (e.g. ../../etc/passwd)
    name = os.path.basename(name)
    name = name.replace("\\", "/")
    name = os.path.basename(name)

    # Remove control characters (newlines, tabs, etc.)
    name = "".join(ch for ch in name if unicodedata.category(ch) != "Cc")

    # Strip characters outside the safe allowlist
    name = _ALLOWED_CHARS.sub("", name)

    # Collapse repeated whitespace and trim leading/trailing dots/spaces
    name = re.sub(r"\s+", " ", name).strip(" .")

    if not name or set(name) <= {"."}:
        return "unnamed_file"

    # Enforce UTF-8 byte limit on the base (name without extension), preserving the extension.
    base, ext = os.path.splitext(name)
    if len(ext.encode("utf-8", errors="replace")) > 64:  # pathological extension
        base, ext = name, ""

    # Truncate the base filename to max_base_bytes (UTF-8), preserving extension.
    base_bytes = base.encode("utf-8", errors="replace")
    if len(base_bytes) > max_base_bytes:
        base = base_bytes[:max_base_bytes].decode("utf-8", errors="ignore")

    sanitized = base + ext
    if not sanitized or set(sanitized) <= {"."}:
        return "unnamed_file"

    return sanitized


def format_directory_template(template: str, channel: str, message_date: datetime, media_type: str, ext: str) -> str:
    """Interpolate tokens ({channel}, {year}, {month}, {day}, {type}, {ext})
    into a clean subfolder path."""
    safe_channel = sanitize_filename(channel).strip() or "unknown"
    safe_type = sanitize_filename(media_type).strip() or "media"
    safe_ext = sanitize_filename(ext).lstrip(".").strip()

    mapping = {
        "channel": safe_channel,
        "year": f"{message_date.year:04d}",
        "month": f"{message_date.month:02d}",
        "day": f"{message_date.day:02d}",
        "type": safe_type,
        "ext": safe_ext,
    }

    parts = []
    for segment in template.split("/"):
        segment = segment.strip()
        if not segment:
            continue
        for token, value in mapping.items():
            segment = segment.replace("{" + token + "}", value)
        # Keep only directory-safe characters
        segment = sanitize_filename(segment).strip()
        if segment:
            parts.append(segment)

    if not parts:
        return safe_channel

    # Prevent the final path from ever escaping its root
    return os.path.normpath("/".join(parts))


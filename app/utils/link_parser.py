import re
import urllib.parse
from typing import Optional, Tuple, List, Union


def parse_single_telegram_link(link_str: str) -> Optional[Tuple[Union[str, int], int]]:
    """
    Parse a Telegram message link or tg:// URI.
    Returns (channel_identifier, message_id) or None if invalid.
    
    Examples:
      - https://t.me/durov/123 -> ('durov', 123)
      - https://t.me/c/1234567890/456 -> (-1001234567890, 456)
      - t.me/c/1234567890/456?single -> (-1001234567890, 456)
      - tg://resolve?domain=channel&post=789 -> ('channel', 789)
    """
    if not link_str or not isinstance(link_str, str):
        return None

    raw = link_str.strip()

    # Handle tg:// protocol
    if raw.startswith("tg://"):
        try:
            parsed = urllib.parse.urlparse(raw)
            qs = urllib.parse.parse_qs(parsed.query)
            if "domain" in qs and "post" in qs:
                domain = qs["domain"][0].strip().lstrip("@")
                post_id = int(qs["post"][0].strip())
                return domain, post_id
        except Exception:
            return None

    # Strip http://, https://, www.
    clean = re.sub(r"^https?://(www\.)?", "", raw)

    # Must start with t.me/ or telegram.me/
    match_prefix = re.match(r"^(t\.me|telegram\.me)/(.*)$", clean)
    if not match_prefix:
        return None

    path_and_query = match_prefix.group(2)
    # Strip query parameters (?single, ?comment=...) and fragments
    path = path_and_query.split("?")[0].split("#")[0].strip("/")

    # Format 1: Private channel (e.g. c/1234567890/456)
    c_match = re.match(r"^c/(\d+)/(\d+)$", path)
    if c_match:
        raw_channel_id = c_match.group(1)
        post_id = int(c_match.group(2))
        # Telegram Web represents private supergroups without the -100 prefix
        peer_id = int(f"-100{raw_channel_id}")
        return peer_id, post_id

    # Format 2: Public channel or user (e.g. mychannel/456)
    pub_match = re.match(r"^([a-zA-Z0-9_]{4,32})/(\d+)$", path)
    if pub_match:
        channel_user = pub_match.group(1)
        post_id = int(pub_match.group(2))
        return channel_user, post_id

    return None


def parse_telegram_links(text_or_list: Union[str, List[str]]) -> List[Tuple[Union[str, int], int]]:
    """
    Parse multiple Telegram links from a string (newline/comma separated) or a list.
    Deduplicates results while preserving order.
    """
    if isinstance(text_or_list, str):
        # Split on newlines, commas, spaces
        candidates = re.split(r"[\r\n,]+", text_or_list)
    else:
        candidates = text_or_list

    results = []
    seen = set()

    for item in candidates:
        tokens = item.strip().split()
        for token in tokens:
            parsed = parse_single_telegram_link(token)
            if parsed and parsed not in seen:
                seen.add(parsed)
                results.append(parsed)

    return results


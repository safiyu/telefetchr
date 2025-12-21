import unicodedata

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

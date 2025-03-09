import logging
import os
import re

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)


def normalize_path(p: str) -> str:
    """Normalize file paths (convert backslashes to slashes)."""
    return os.path.normpath(p).replace("\\", "/")


def sanitize_filename(name: str) -> str:
    """Sanitize the filename by replacing characters not allowed in filenames."""
    sanitized = re.sub(r'[\\/*?:"<>|]', "_", name)
    logger.debug(f"Sanitized filename: {sanitized}")
    return sanitized

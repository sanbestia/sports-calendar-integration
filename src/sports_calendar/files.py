"""File writes that never leave half a file behind."""
import os


def write_atomically(path: str, text: str) -> None:
    """Write text to path through a temporary file next to it: a crash mid-write leaves the old file, never half a file."""
    temporary = f"{path}.tmp"
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):  # only after a failed write: os.replace has moved it otherwise
            os.remove(temporary)

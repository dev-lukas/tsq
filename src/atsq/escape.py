"""ServerQuery value escaping.

The TeamSpeak ServerQuery protocol transports values with a fixed escape
table (see the TS3 ServerQuery manual). The replacement order matters:
the backslash must be (un)escaped first/last respectively, otherwise
escape sequences produced by earlier replacements get corrupted.
"""

from __future__ import annotations

__all__ = ["escape", "unescape"]

# (raw, escaped) pairs. Order matters - backslash first.
_ESCAPE_MAP: tuple[tuple[str, str], ...] = (
    ("\\", r"\\"),
    ("/", r"\/"),
    (" ", r"\s"),
    ("|", r"\p"),
    ("\a", r"\a"),
    ("\b", r"\b"),
    ("\f", r"\f"),
    ("\n", r"\n"),
    ("\r", r"\r"),
    ("\t", r"\t"),
    ("\v", r"\v"),
)


def escape(value: str) -> str:
    """Escape a value for transport: ``escape("Hello World")`` → ``"Hello\\sWorld"``."""
    for char, replacement in _ESCAPE_MAP:
        value = value.replace(char, replacement)
    return value


# Escape char (the char after the backslash) -> raw char.
_UNESCAPE_MAP: dict[str, str] = {escaped[1]: raw for raw, escaped in _ESCAPE_MAP}


def unescape(value: str) -> str:
    """Undo transport escaping: ``unescape("Hello\\sWorld")`` → ``"Hello World"``.

    Implemented as a single left-to-right pass instead of sequential
    ``str.replace`` calls: replace-based unescaping corrupts sequences like
    ``\\\\s`` (escaped backslash + literal ``s``), which must decode to
    ``\\s``, not to ``\\`` + space. (py-ts3 has exactly that bug.)
    """
    if "\\" not in value:
        return value
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        char = value[i]
        if char == "\\" and i + 1 < n:
            mapped = _UNESCAPE_MAP.get(value[i + 1])
            if mapped is not None:
                out.append(mapped)
                i += 2
                continue
        out.append(char)
        i += 1
    return "".join(out)

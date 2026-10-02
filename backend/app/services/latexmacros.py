"""Parsing the LaTeX macros of the text the import writes: brace matching, `\\name[opt]{a}{b}…`, and the three picture
commands (`\\lectaimage`, `\\lectaimagewithtext`, `\\lectaimagepair`) that the import, the HTML draft and the
plain-text extraction all have to understand in the same way."""

from __future__ import annotations

import re
from typing import Callable


def match_brace(s: str, i: int, open_c: str = "{", close_c: str = "}") -> int:
    """Index just after the group that opens at s[i] (or -1)."""
    if i >= len(s) or s[i] != open_c:
        return -1
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c == "\\":
            j += 2
            continue
        if c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def macro_spans(src: str, name: str, n_required: int) -> list[tuple[int, int, str | None, list[str]]]:
    """Every `\\name[opt]{a}{b}…` as (start, end, optional, [required args])."""
    out = []
    pat = re.compile(r"\\" + re.escape(name) + r"(?![A-Za-z@])")
    for m in pat.finditer(src):
        j = m.end()
        while j < len(src) and src[j] in " \t":
            j += 1
        opt = None
        if j < len(src) and src[j] == "[":
            k = match_brace(src, j, "[", "]")
            if k < 0:
                continue
            opt, j = src[j + 1 : k - 1], k
            while j < len(src) and src[j] in " \t":
                j += 1
        args: list[str] = []
        ok = True
        for _ in range(n_required):
            while j < len(src) and src[j] in " \t\n":
                j += 1
            k = match_brace(src, j)
            if k < 0:
                ok = False
                break
            args.append(src[j + 1 : k - 1])
            j = k
        if ok:
            out.append((m.start(), j, opt, args))
    return out


def replace_macro(src: str, name: str, n_required: int, fn) -> str:  # noqa: ANN001
    spans = macro_spans(src, name, n_required)
    if not spans:
        return src
    out, pos = [], 0
    for start, end, opt, args in spans:
        if start < pos:
            continue
        out.append(src[pos:start])
        out.append(fn(opt, args))
        pos = end
    out.append(src[pos:])
    return "".join(out)




# --------------------------------------------------------------------------- pictures

# command → (number of required arguments, takes an optional [caption])
IMAGE_COMMANDS: dict[str, tuple[int, bool]] = {
    "lectaimage": (1, True),  # [caption]{image}
    "lectaimagewithtext": (2, True),  # [caption]{image}{text beside it}
    "lectaimagepair": (4, False),  # {caption 1}{image 1}{caption 2}{image 2}
}
# Which of the required arguments name a picture (the others are captions or text).
IMAGE_ARGS: dict[str, tuple[int, ...]] = {"lectaimage": (0,), "lectaimagewithtext": (0,), "lectaimagepair": (1, 3)}


def image_spans(src: str) -> list[tuple[int, int, str, str | None, list[str]]]:
    """Every picture command as (start, end, name, optional caption, required arguments), in order."""
    out = []
    for name in IMAGE_COMMANDS:
        n_args, _has_opt = IMAGE_COMMANDS[name]
        for start, end, opt, args in macro_spans(src, name, n_args):
            out.append((start, end, name, opt, args))
    out.sort(key=lambda t: t[0])
    return out


def map_images(src: str, fn: Callable[[str], str | None]) -> str:
    """Rewrite the picture names in `\\lectaimage…` commands: fn(name) returns the new name, "" when the picture goes away
    (a lone picture disappears with its command; beside its text or in a pair the command stays, with an empty name,
    so the text and the other picture are kept), or None to leave the name as it is."""
    out, pos = [], 0
    for start, end, name, opt, args in image_spans(src):
        if start < pos:
            continue
        new = list(args)
        for i in IMAGE_ARGS[name]:
            r = fn(args[i].strip())
            if r is not None:
                new[i] = r
        if name == "lectaimage" and new[0] == "":
            out.append(src[pos:start])
            pos = end
            continue
        head = f"\\{name}" + (f"[{opt}]" if opt is not None else "")
        out.append(src[pos:start] + head + "".join("{" + a + "}" for a in new))
        pos = end
    out.append(src[pos:])
    return "".join(out)


def image_names(src: str) -> list[str]:
    """The pictures a text uses, in order (empty names left out)."""
    return [args[i].strip() for _s, _e, name, _o, args in image_spans(src) for i in IMAGE_ARGS[name] if args[i].strip()]


def without_images(src: str) -> str:
    """The text with the picture commands replaced by what is written around them (text beside a picture, captions)."""
    out, pos = [], 0
    for start, end, name, opt, args in image_spans(src):
        if start < pos:
            continue
        if name == "lectaimage":
            keep = [opt or ""]
        elif name == "lectaimagewithtext":
            keep = [opt or "", args[1]]
        else:
            keep = [args[0], args[2]]
        out.append(src[pos:start] + " ".join(k for k in keep if k.strip()))
        pos = end
    out.append(src[pos:])
    return "".join(out)

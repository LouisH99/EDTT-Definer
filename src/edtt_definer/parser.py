from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from .model import EntryKind, EntryNode, EventDocument, RawNode, SectionNode


OPEN_SECTION_RE = re.compile(r"^\[([^\\\]]+)\]\s*$")
CLOSE_SECTION_RE = re.compile(r"^\[\\([^\]]+)\]\s*$")
KEY_VALUE_RE = re.compile(r"^\s*([^=\s][^=]*?)\s*=\s*(.*)$")


class ParseError(ValueError):
    pass


def _entry_from_line(line: str) -> EntryNode:
    stripped = line.strip()
    if not stripped:
        return EntryNode(line, EntryKind.BLANK)
    if stripped.startswith(("<!--", "<!–", "#", ";")):
        return EntryNode(line, EntryKind.COMMENT)
    match = KEY_VALUE_RE.match(line)
    if match:
        key, value = match.groups()
        comment_at = value.find("<!--")
        suffix = ""
        if comment_at >= 0:
            logical_value = value[:comment_at].rstrip()
            suffix = value[len(logical_value):]
        else:
            logical_value = value.strip()
        return EntryNode(
            line,
            EntryKind.KEY_VALUE,
            key.strip(),
            logical_value,
            suffix,
        )
    return EntryNode(line, EntryKind.RAW)


def parse_text(text: str, *, encoding: str = "utf-8") -> EventDocument:
    newline = "\r\n" if "\r\n" in text else "\n"
    trailing_newline = text.endswith(("\r\n", "\n", "\r"))
    lines = text.splitlines()
    nodes: list[RawNode | SectionNode] = []
    current: SectionNode | None = None

    for line in lines:
        if current is None:
            open_match = OPEN_SECTION_RE.match(line.strip())
            if open_match:
                name = open_match.group(1).strip()
                if name.casefold() == "event":
                    nodes.append(RawNode(line))
                else:
                    current = SectionNode(name=name, opener_raw=line, closer_raw=f"[\\{name}]")
            else:
                nodes.append(RawNode(line))
            continue

        close_match = CLOSE_SECTION_RE.match(line.strip())
        if close_match and close_match.group(1).strip().casefold() == current.name.casefold():
            current.closer_raw = line
            nodes.append(current)
            current = None
        else:
            current.entries.append(_entry_from_line(line))

    if current is not None:
        current.closer_raw = ""
        nodes.append(current)

    return EventDocument(
        nodes=nodes,
        newline=newline,
        trailing_newline=trailing_newline,
        encoding=encoding,
    )


def serialize_document(document: EventDocument) -> str:
    lines: list[str] = []
    for node in document.nodes:
        if isinstance(node, RawNode):
            lines.append(node.raw)
            continue
        lines.append(node.opener_raw or f"[{node.name}]")
        lines.extend(entry.raw for entry in node.entries)
        if node.closer_raw:
            lines.append(node.closer_raw)
    result = document.newline.join(lines)
    if document.trailing_newline:
        result += document.newline
    return result


def load_document(path: str | Path) -> EventDocument:
    raw = Path(path).read_bytes()
    if raw.startswith(b"\xef\xbb\xbf"):
        return parse_text(raw.decode("utf-8-sig"), encoding="utf-8-sig")
    try:
        return parse_text(raw.decode("utf-8"), encoding="utf-8")
    except UnicodeDecodeError:
        return parse_text(raw.decode("cp1252"), encoding="cp1252")


def document_bytes(document: EventDocument) -> bytes:
    text = serialize_document(document)
    try:
        return text.encode(document.encoding)
    except UnicodeEncodeError:
        document.encoding = "utf-8"
        return text.encode("utf-8")


def atomic_write(path: str | Path, data: bytes) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=f".{destination.name}.", dir=destination.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, destination)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def save_document(document: EventDocument, path: str | Path) -> None:
    atomic_write(path, document_bytes(document))

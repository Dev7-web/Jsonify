from __future__ import annotations

import hashlib
import re
from typing import Any


FINGERPRINT_VERSION = "headers-v1"

_WHITESPACE_PATTERN = re.compile(r"\s+")


def build_header_structure_fingerprint(header_structure: dict[str, Any]) -> str:
    labels = sorted(_collect_header_labels(header_structure))
    if not labels:
        raise ValueError("Approved header structure must contain at least one header or field.")

    digest_source = "\n".join(labels).encode("utf-8")
    digest = hashlib.sha256(digest_source).hexdigest()
    return f"{FINGERPRINT_VERSION}:{digest}"


def _collect_header_labels(header_structure: dict[str, Any]) -> set[str]:
    labels: set[str] = set()

    for sheet in header_structure.get("sheets", []):
        for section in sheet.get("sections", []):
            _add_label(labels, section.get("title"))

            for field in section.get("fields", []):
                _add_label(labels, field)

            for header in section.get("headers", []):
                _collect_header_node_labels(labels, header)

    return labels


def _collect_header_node_labels(labels: set[str], header: dict[str, Any]) -> None:
    _add_label(labels, header.get("name"))

    for subheader in header.get("subheaders", []):
        _collect_header_node_labels(labels, subheader)


def _add_label(labels: set[str], value: Any) -> None:
    if not isinstance(value, str):
        return

    label = _normalize_label(value)
    if label:
        labels.add(label)


def _normalize_label(value: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", value).strip().casefold()

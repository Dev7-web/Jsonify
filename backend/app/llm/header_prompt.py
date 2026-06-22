from __future__ import annotations

import json
import re
from typing import Any, Literal, TypedDict, cast

from app.llm.client import acall_llm, call_llm, call_llm_with_pii


SectionType = Literal["key_value", "table"]


class HeaderNode(TypedDict, total=False):
    name: str
    subheaders: list["HeaderNode"]


class LayoutSection(TypedDict, total=False):
    type: SectionType
    title: str
    fields: list[str]
    headers: list[HeaderNode]


class SheetLayout(TypedDict):
    name: str
    sections: list[LayoutSection]


class HeaderStructure(TypedDict):
    sheets: list[SheetLayout]


class HeaderStructureParseError(ValueError):
    pass


SYSTEM_PROMPT = """You identify document layout headers from flattened spreadsheets.

Return only valid JSON. Do not wrap the JSON in Markdown.
Do not explain your answer.
Copy header, field, and section text verbatim from the provided cells.
Do not invent labels that are not present in the flattened workbook text.
Represent form-style areas as type "key_value".
Represent column-header tables as type "table".
Use nested "subheaders" only when the workbook visually groups columns under a parent header.
When form-style group titles appear inside a larger page title, each group title is
its own key_value section. Do not put group titles inside another section's fields.
"""


USER_PROMPT_TEMPLATE = """Identify the reusable header structure in this flattened Excel workbook.

Return JSON in exactly this shape:
{
  "sheets": [
    {
      "name": "Sheet name copied exactly",
      "sections": [
        {
          "type": "key_value",
          "title": "Section title copied exactly",
          "fields": ["Field label copied exactly"]
        },
        {
          "type": "table",
          "title": "Section title copied exactly",
          "headers": [
            {
              "name": "Column header copied exactly",
              "subheaders": [
                { "name": "Nested column header copied exactly" }
              ]
            }
          ]
        }
      ]
    }
  ]
}

Rules:
- Return only JSON.
- Include every sheet in the flattened workbook.
- A sheet may have an empty "sections" array if it has no reusable layout headers.
- For "key_value" sections, include only reusable label names in "fields"; do not include their values.
- If a form area has visible group titles on the same row or nearby rows, each group title is a separate "key_value" section.
- Do not use a larger page/report title as the section title when smaller group titles contain the actual field labels.
- Do not include sibling group titles as fields. Example: if "Amendment Information" and "Lease Information" are group titles, they should be section titles, not fields under "Amendment Abstract".
- For "table" sections, include column headers in left-to-right order.
- If a section or table has no visible title in the cells, omit the "title" key; never return an empty title.
- Preserve the exact sheet names from lines that start with "## Sheet:".
- Preserve exact header text from the cells, but omit cell coordinates in the JSON.

Flattened workbook:
{flattened_text}
"""

CODE_FENCE_PATTERN = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL)
HEADER_STRUCTURE_PARSE_ATTEMPTS = 2


def build_header_detection_messages(flattened_text: str) -> tuple[str, str]:
    if not flattened_text.strip():
        raise ValueError("flattened_text must not be empty.")

    return SYSTEM_PROMPT, build_header_detection_user_prompt(flattened_text)


def build_header_detection_user_prompt(flattened_text: str) -> str:
    return USER_PROMPT_TEMPLATE.replace("{flattened_text}", flattened_text)


# ponytail: full flattened workbook passed in one shot. Safe under the 25 MB
# upload limit and Gemini's large context window. Split if uploads ever grow.
def detect_header_structure(flattened_text: str) -> HeaderStructure:
    system, user = build_header_detection_messages(flattened_text)
    response = call_llm(system, user, response_mime_type="application/json")
    return parse_header_structure(response)


async def adetect_header_structure(flattened_text: str) -> HeaderStructure:
    system, user = build_header_detection_messages(flattened_text)
    response = await acall_llm(system, user, response_mime_type="application/json")
    return parse_header_structure(response)


async def adetect_header_structure_from_cell_map(
    cell_map: dict[str, str],
    *,
    pii_document_id_callback: Any | None = None,
) -> HeaderStructure:
    last_error: HeaderStructureParseError | None = None

    for _ in range(HEADER_STRUCTURE_PARSE_ATTEMPTS):
        response = await call_llm_with_pii(
            SYSTEM_PROMPT,
            cell_map,
            build_user_prompt=build_header_detection_user_prompt,
            pii_document_id_callback=pii_document_id_callback,
            response_mime_type="application/json",
        )
        try:
            return parse_header_structure(response)
        except HeaderStructureParseError as error:
            last_error = error

    if last_error is not None:
        raise last_error

    raise HeaderStructureParseError("LLM response could not be parsed.")


def parse_header_structure(raw_text: str) -> HeaderStructure:
    json_text = _strip_code_fence(raw_text)

    try:
        parsed = json.loads(json_text)
    except json.JSONDecodeError as error:
        raise HeaderStructureParseError(f"LLM response is not valid JSON: {error}") from error

    return validate_header_structure(parsed)


def validate_header_structure(value: Any) -> HeaderStructure:
    if not isinstance(value, dict):
        raise HeaderStructureParseError("Header structure must be a JSON object.")

    sheets = value.get("sheets")
    if not isinstance(sheets, list):
        raise HeaderStructureParseError('Header structure must contain a "sheets" array.')

    normalized_sheets = [_validate_sheet(sheet, index) for index, sheet in enumerate(sheets)]
    return {"sheets": normalized_sheets}


def _validate_sheet(value: Any, index: int) -> SheetLayout:
    location = f"sheets[{index}]"

    if not isinstance(value, dict):
        raise HeaderStructureParseError(f"{location} must be an object.")

    name = value.get("name")
    if not _is_non_empty_string(name):
        raise HeaderStructureParseError(f'{location}.name must be a non-empty string.')

    sections = value.get("sections")
    if not isinstance(sections, list):
        raise HeaderStructureParseError(f'{location}.sections must be an array.')

    return {
        "name": name.strip(),
        "sections": [
            _validate_section(section, f"{location}.sections[{section_index}]")
            for section_index, section in enumerate(sections)
        ],
    }


def _validate_section(value: Any, location: str) -> LayoutSection:
    if not isinstance(value, dict):
        raise HeaderStructureParseError(f"{location} must be an object.")

    section_type = value.get("type")
    if section_type not in ("key_value", "table"):
        raise HeaderStructureParseError(
            f'{location}.type must be either "key_value" or "table".'
        )

    title = _normalize_optional_title(value.get("title"), location)

    section: LayoutSection = {"type": cast(SectionType, section_type)}
    if title is not None:
        section["title"] = title

    if section_type == "key_value":
        fields = _validate_string_list(value.get("fields"), f"{location}.fields")
        section["fields"] = fields
        return section

    headers = _validate_header_list(value.get("headers"), f"{location}.headers")
    section["headers"] = headers
    return section


def _validate_header_list(value: Any, location: str) -> list[HeaderNode]:
    if not isinstance(value, list):
        raise HeaderStructureParseError(f"{location} must be an array.")

    if not value:
        raise HeaderStructureParseError(f"{location} must not be empty.")

    return [
        _validate_header_node(header, f"{location}[{index}]")
        for index, header in enumerate(value)
    ]


def _validate_header_node(value: Any, location: str) -> HeaderNode:
    if not isinstance(value, dict):
        raise HeaderStructureParseError(f"{location} must be an object.")

    name = value.get("name")
    if not _is_non_empty_string(name):
        raise HeaderStructureParseError(f"{location}.name must be a non-empty string.")

    header: HeaderNode = {"name": name.strip()}

    if "subheaders" in value:
        header["subheaders"] = _validate_header_list(
            value.get("subheaders"),
            f"{location}.subheaders",
        )

    return header


def _validate_string_list(value: Any, location: str) -> list[str]:
    if not isinstance(value, list):
        raise HeaderStructureParseError(f"{location} must be an array.")

    strings: list[str] = []
    for index, item in enumerate(value):
        if not _is_non_empty_string(item):
            raise HeaderStructureParseError(
                f"{location}[{index}] must be a non-empty string."
            )

        strings.append(item.strip())

    return strings


def _strip_code_fence(raw_text: str) -> str:
    match = CODE_FENCE_PATTERN.search(raw_text)
    if match:
        return match.group(1).strip()

    return raw_text.strip()


def _is_non_empty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normalize_optional_title(value: Any, location: str) -> str | None:
    if value is None:
        return None

    if not isinstance(value, str):
        raise HeaderStructureParseError(
            f"{location}.title must be a string when present."
        )

    title = value.strip()
    if not title:
        return None

    return title

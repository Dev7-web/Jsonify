from pathlib import Path
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models import DocumentSchema
from app.pipeline.fingerprint import build_header_label_fingerprint
from app.pipeline.fingerprint import build_header_structure_fingerprint
from app.pipeline.fingerprint import find_best_schema_match


DOLLAR_TREE_APPROVED = {
    "sheets": [
        {
            "name": "Original Lease - Lease",
            "sections": [
                {
                    "type": "key_value",
                    "title": "Amendment Abstract",
                    "fields": [
                        "Lease ID",
                        "DBA",
                        "Type",
                        "Lease",
                        "Status",
                    ],
                },
                {
                    "type": "table",
                    "title": "Space",
                    "headers": [
                        {"name": "Unit"},
                        {"name": "Building"},
                        {"name": "Area"},
                    ],
                },
                {
                    "type": "table",
                    "title": "Charge Schedules",
                    "headers": [
                        {"name": "Charge Code"},
                        {"name": "Charge Desc"},
                        {"name": "Date From"},
                        {"name": "Date To"},
                        {"name": "Monthly Amt"},
                        {"name": "Annual Amt"},
                    ],
                },
                {
                    "type": "table",
                    "title": "Late Fee",
                    "headers": [
                        {"name": "Start Date"},
                        {"name": "End Date"},
                        {"name": "Amount"},
                    ],
                },
            ],
        }
    ]
}

DOLLAR_TREE_FILE_ONE = {
    "sheets": [
        {
            "name": "Original Lease - Lease",
            "sections": [
                {
                    "type": "key_value",
                    "title": "Amendment Abstract",
                    "fields": ["Lease ID", "DBA", "Type", "Lease", "Status"],
                },
                {
                    "type": "table",
                    "title": "Space",
                    "headers": [{"name": "Unit"}, {"name": "Building"}, {"name": "Area"}],
                },
                {
                    "type": "table",
                    "title": "Charge Schedules",
                    "headers": [
                        {"name": "Charge Code"},
                        {"name": "Charge Desc"},
                        {"name": "Date From"},
                        {"name": "Date To"},
                        {"name": "Monthly Amt"},
                        {"name": "Annual Amt"},
                    ],
                },
            ],
        }
    ]
}

DOLLAR_TREE_FILE_TWO = {
    "sheets": [
        {
            "name": "Renewal - 1st Renewal",
            "sections": [
                {
                    "type": "key_value",
                    "title": "Amendment Abstract",
                    "fields": ["Lease ID", "DBA", "Type", "Lease"],
                },
                {
                    "type": "table",
                    "title": "Charge Schedules",
                    "headers": [
                        {"name": "Charge Code"},
                        {"name": "Charge Desc"},
                        {"name": "Date From"},
                        {"name": "Date To"},
                        {"name": "Monthly Amt"},
                        {"name": "Annual Amt"},
                        {"name": "Notes"},
                    ],
                },
                {
                    "type": "table",
                    "title": "Late Fee",
                    "headers": [{"name": "Start Date"}, {"name": "End Date"}, {"name": "Amount"}],
                },
            ],
        }
    ]
}

UNRELATED_FILE = {
    "sheets": [
        {
            "name": "Employees",
            "sections": [
                {
                    "type": "table",
                    "title": "Employee Roster",
                    "headers": [
                        {"name": "Employee ID"},
                        {"name": "First Name"},
                        {"name": "Last Name"},
                        {"name": "Department"},
                        {"name": "Manager"},
                        {"name": "Salary"},
                    ],
                }
            ],
        }
    ]
}


def main() -> None:
    approved_schema = DocumentSchema(
        name="Dollar Tree approved layout",
        file_type="xlsx",
        fingerprint=build_header_structure_fingerprint(DOLLAR_TREE_APPROVED),
        version=1,
        status="active",
        header_structure=DOLLAR_TREE_APPROVED,
    )

    file_one_match = find_best_schema_match(
        build_header_label_fingerprint(DOLLAR_TREE_FILE_ONE),
        [approved_schema],
    )
    file_two_match = find_best_schema_match(
        build_header_label_fingerprint(DOLLAR_TREE_FILE_TWO),
        [approved_schema],
    )
    unrelated_match = find_best_schema_match(
        build_header_label_fingerprint(UNRELATED_FILE),
        [approved_schema],
    )

    assert file_one_match is not None
    assert file_two_match is not None
    assert file_one_match.schema.id == approved_schema.id
    assert file_two_match.schema.id == approved_schema.id
    assert unrelated_match is None

    print("Fingerprint matching check passed.")
    print(f"File one score: {file_one_match.similarity.score}")
    print(f"File two score: {file_two_match.similarity.score}")
    print("Unrelated file score: no match")


if __name__ == "__main__":
    main()

from pathlib import Path
import math
import sys


sys.path.append(str(Path(__file__).resolve().parents[1]))

from app.models import DocumentSchema
from app.pipeline.fingerprint import HeaderFingerprint
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

NOISY_REBO_APPROVED = {
    "sheets": [
        {
            "name": "Lease_Abstract (1)",
            "sections": [
                {
                    "type": "key_value",
                    "title": "Property Information:",
                    "fields": [
                        "Property Name :",
                        "Address 1 :",
                        "Address 2 :",
                        "City :",
                        "State :",
                        "Zip :",
                        "Country :",
                        "Currency :",
                    ],
                },
                {
                    "type": "key_value",
                    "title": "Tenant Information:",
                    "fields": [
                        "Lease Status :",
                        "Rentable SF :",
                        "Space Type :",
                        "Usable SF :",
                        "Recovery Type :",
                        "Trade Name :",
                    ],
                },
                {
                    "type": "table",
                    "title": "Rent Schedule:",
                    "headers": [
                        {"name": "Rent Type"},
                        {"name": "Begin Date"},
                        {"name": "End Date"},
                        {"name": "Monthly"},
                        {"name": "Annual"},
                        {"name": "SF"},
                        {"name": "PSF/Year"},
                    ],
                },
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

    noisy_rebo_schema = DocumentSchema(
        name="Noisy REBO approved layout",
        file_type="xlsx",
        fingerprint=build_header_structure_fingerprint(NOISY_REBO_APPROVED),
        version=1,
        status="active",
        header_structure=NOISY_REBO_APPROVED,
    )
    noisy_rebo_schema_fingerprint = build_header_label_fingerprint(
        noisy_rebo_schema.header_structure
    )
    partial_noisy_rebo_labels = sorted(noisy_rebo_schema_fingerprint.labels)[
        : math.ceil(len(noisy_rebo_schema_fingerprint.labels) * 0.83)
    ]
    noisy_rebo_candidate = HeaderFingerprint(
        fingerprint="headers-v1:noisy-rebo-candidate",
        labels=frozenset(
            {
                *partial_noisy_rebo_labels,
                "6051 n street 2nd floor 201 fresno",
                "active",
                "office",
                "$4,496.61",
                "$56,117.64",
                "01/03/2020",
                "02/28/2025",
                "lease is silent",
                "see art.13 of lease",
                "none",
                "pacific clinics",
                "usa",
                "usd",
                "{{property name_subsummarization}}",
                "{{rent notes_subsection no}}: {{rent notes_subsummarization}}",
                "long clause value that is not a reusable header",
                "another tenant-specific value",
                "current term",
                "total lease term",
                "base rent",
                "standard industrial commercial multi tenant lease gross",
                "third amendment to lease",
                "letter dated 08312023",
                "change of ownership",
                "notice",
                "addendum",
                "agreement",
                "certificate",
                "tenant",
                "landlord/payee",
                "payee",
                "contact copy value",
                "unrelated row value one",
                "unrelated row value two",
                "unrelated row value three",
                "unrelated row value four",
                "unrelated row value five",
                "unrelated row value six",
                "unrelated row value seven",
                "unrelated row value eight",
                "unrelated row value nine",
                "unrelated row value ten",
            }
        ),
    )
    noisy_rebo_match = find_best_schema_match(
        noisy_rebo_candidate,
        [noisy_rebo_schema],
    )

    assert file_one_match is not None
    assert file_two_match is not None
    assert noisy_rebo_match is not None
    assert file_one_match.schema.id == approved_schema.id
    assert file_two_match.schema.id == approved_schema.id
    assert noisy_rebo_match.schema.id == noisy_rebo_schema.id
    assert unrelated_match is None

    print("Fingerprint matching check passed.")
    print(f"File one score: {file_one_match.similarity.score}")
    print(f"File two score: {file_two_match.similarity.score}")
    print(f"Noisy REBO score: {noisy_rebo_match.similarity.score}")
    print("Unrelated file score: no match")


if __name__ == "__main__":
    main()

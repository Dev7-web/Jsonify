from fastapi import UploadFile

from app.services.excel_header_detector_service import detect_headers_from_excel
from app.schemas.excel_schema import HeaderDetectionApiResponse

async def detect_excel_headers_controller(
    file: UploadFile
) -> HeaderDetectionApiResponse:
    if not file.filename:
        return HeaderDetectionApiResponse(
            success=False,
            message="Excel file is required"
        )

    allowed_extensions = [".xlsx", ".xlsm"]

    is_valid_excel = any(
        file.filename.lower().endswith(extension)
        for extension in allowed_extensions
    )

    if not is_valid_excel:
        return HeaderDetectionApiResponse(
            success=False,
            message="Only .xlsx and .xlsm files are supported right now"
        )

    file_bytes = await file.read()

    result = detect_headers_from_excel(file_bytes)

    return HeaderDetectionApiResponse(
        success=True,
        message="Headers detected successfully",
        data=result
    )


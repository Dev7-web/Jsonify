from fastapi import APIRouter, File, UploadFile, HTTPException

from app.controllers.excel_controller import detect_excel_headers_controller
from app.schemas.excel_schema import HeaderDetectionApiResponse

router = APIRouter()


@router.post(
    "/detect-headers",
    response_model=HeaderDetectionApiResponse
)
async def detect_excel_headers(file: UploadFile = File(...)):
    try:
        return await detect_excel_headers_controller(file)

    except Exception as error:
        raise HTTPException(
            status_code=500,
            detail=str(error)
        )
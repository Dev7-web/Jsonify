from typing import Any,Dict,List,Optional

from pydantic import BaseModel

class DetectedHeader(BaseModel):
    name:str
    column_index:int
    excel_column_names:str
    
    
class HeaderDetectionResult(BaseModel):
        sheet_name:str
        header_row_index:int
        header_row_number:int
        confidence_score:int
        headers:List[DetectedHeader]
        preview_rows:List[Dict[str,Any]]

        
class HeaderDetectionApiResponse(BaseModel):
    success:bool
    message:str
    data:Optional[HeaderDetectionResult] = None
    
    
        
        
    
    
    
    
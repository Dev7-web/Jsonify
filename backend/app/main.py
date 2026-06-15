# Entry Point of Fast API Backend
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app=FastAPI(
    title="REBackoffice Project",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_headers=["*"],
    allow_credentials=True,
    allow_methods=["*"]
    
)

app.include_router(excel_router,prefix="/api/excel",tags=["All APIs"])


@app.get("/")
def health_check():
    return{
        "success":True,
        "message":"Backend is running"
    }

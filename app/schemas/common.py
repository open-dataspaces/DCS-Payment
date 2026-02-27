from app.schemas.base import BaseSchema

class ErrorResponse(BaseSchema):
    type: str
    title: str
    detail: str
    status: int
    instance: str

from pydantic import BaseModel


class JobAcceptedResponse(BaseModel):
    job_id: str
    status: str = "accepted"
    message: str = "El procesamiento ha comenzado en segundo plano."

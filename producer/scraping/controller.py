from dependencies.dependencies import get_scraping_orchestrator
from fastapi import Depends
from scraping.services.scraping_service import ScrapingOrchestrator
from fastapi import BackgroundTasks
from scraping.models.bulk_task import BulkTaskRequest
from scraping.models.job_accepted_response import JobAcceptedResponse
from typing import Any
from fastapi import APIRouter
from starlette import status

routesScrapingTasks = APIRouter(
    prefix="/scraping",
    tags=["Endpoints para la ingesta de datos para el scraping"],
)


# Añado una tarea
@routesScrapingTasks.post(
    "/tasks",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobAcceptedResponse,
)
async def create_scraping_job(
    request: BulkTaskRequest,
    background_tasks: BackgroundTasks,
    orchestrator: ScrapingOrchestrator = Depends(get_scraping_orchestrator),
) -> JobAcceptedResponse:

    # Envia la tarea a BackgroundTasks para que la procese de forma asincrona con la peticion
    # Esta se procesa una vez que al usuario ya le hemos dado el OK
    background_tasks.add_task(orchestrator.create_tasks_from_request, request)
    return JobAcceptedResponse(job_id=request.job_id)

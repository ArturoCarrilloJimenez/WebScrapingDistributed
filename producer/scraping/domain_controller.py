from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status

from shared.models.domain_policy import (
    DomainPolicy,
    DomainPolicyCreate,
    DomainPolicyListResponse,
)
from scraping.services.domain_policy_service import DomainPolicyService
from dependencies.dependencies import get_domain_policy_service

domain_router = APIRouter(
    prefix="/domains",
    tags=["Gobernanza y Cumplimiento Normativo por Dominio"],
)


@domain_router.get(
    "",
    status_code=status.HTTP_200_OK,
    summary="Listar todas las politicas de dominio activas",
)
async def list_domain_policies(
    service: Annotated[DomainPolicyService, Depends(get_domain_policy_service)],
) -> DomainPolicyListResponse:
    policies = service.list_policies()
    return DomainPolicyListResponse(total=len(policies), policies=policies)


@domain_router.get(
    "/{domain}",
    status_code=status.HTTP_200_OK,
    summary="Obtener la politica de un dominio especifico",
)
async def get_domain_policy(
    domain: str,
    service: Annotated[DomainPolicyService, Depends(get_domain_policy_service)],
) -> DomainPolicy:
    policy = service.get_policy_by_name(domain)
    if not policy:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No existe una politica explicita registrada para el dominio '{domain}'",
        )
    return policy


@domain_router.put(
    "/{domain}",
    status_code=status.HTTP_201_CREATED,
    summary="Crear o actualizar la politica de un dominio en caliente",
)
async def upsert_domain_policy(
    domain: str,
    policy_data: DomainPolicyCreate,
    service: Annotated[DomainPolicyService, Depends(get_domain_policy_service)],
) -> DomainPolicy:
    clean_domain = domain.strip().lower()
    return await service.upsert_policy(clean_domain, policy_data)


@domain_router.delete(
    "/{domain}",
    status_code=status.HTTP_200_OK,
    summary="Eliminar una politica de dominio",
)
async def delete_domain_policy(
    domain: str,
    service: Annotated[DomainPolicyService, Depends(get_domain_policy_service)],
) -> None:
    clean_domain = domain.strip().lower()
    if clean_domain == "default":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La politica comodin 'default' no puede ser eliminada del sistema",
        )
    success = await service.delete_policy(clean_domain)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Dominio '{domain}' no encontrado",
        )

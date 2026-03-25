from fastapi import APIRouter

from app.routers import agents, documents, health, public_config, settings, streams, topics

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router, tags=["health"])
api_router.include_router(public_config.router)
api_router.include_router(streams.router, prefix="/streams", tags=["streams"])
api_router.include_router(topics.router, prefix="/topics", tags=["topics"])
api_router.include_router(documents.router, tags=["documents"])
api_router.include_router(agents.router)
api_router.include_router(settings.router, prefix="/users", tags=["users"])

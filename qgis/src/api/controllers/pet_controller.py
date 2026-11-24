from typing import Optional

from fastapi import APIRouter

from src.api.requests.placed_objects_request import PlacedObjectsRequest
from src.services.local_pet_service import LocalPETService

router = APIRouter()


@router.get("/status")
def get_status():
    """Polled by the frontend loading indicator (via the backend's /processing-status)."""
    return {"message": "Idle"}


@router.post("/update")
def update_pet_scenario(req: PlacedObjectsRequest, session_id: Optional[str] = None):
    """Recalculate the 3 x 3 wind cells around placed or changed objects for this session."""
    return LocalPETService().update(req.points, session_id)
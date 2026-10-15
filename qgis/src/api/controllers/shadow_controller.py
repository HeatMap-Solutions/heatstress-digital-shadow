from fastapi import APIRouter, HTTPException
from src.api.requests import ShadowMapRequest
from src.services.shadow_service import ShadowService
from src.configs.area import load_area

router = APIRouter()
shadow_service = ShadowService()

@router.post("/map")
def create_hillshade(req: ShadowMapRequest):
    try:
        output_folder = "/data/shadow-maps"
        area = load_area()
        lat, lon = area.lat, area.lon

        shadow_service.generate_hillshade_maps(
            req.dem_path, output_folder, lat, lon, req.start_dt, req.end_dt
        )

        return {
            "status": "success",
            "output_folder": output_folder,
            "message": "Hillshade(s) generated successfully",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error generating hillshade: {str(e)}")

from pydantic import BaseModel, Field
from enum import Enum

class Geometry(Enum): 
    Circle = 'circle'

class Point(BaseModel):
    id: str | None = Field(default=None, min_length=1, max_length=200)
    x: float
    y: float
    height: float
    radius: float
    geometry: Geometry

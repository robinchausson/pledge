from typing import Optional, Any
from pydantic import BaseModel, EmailStr, ConfigDict, field_validator, model_validator


class UserCreate(BaseModel):
    email: EmailStr
    password: str
    role: str = "viewer"
    current_zone: str = "Zone_1"


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    role: str
    current_zone: str = "Zone_1"


class UserZoneUpdate(BaseModel):
    zone_id: str

    @field_validator("zone_id")
    @classmethod
    def validate_zone_id(cls, v: str) -> str:
        if v not in ["Zone_1", "Zone_2"]:
            raise ValueError("zone_id doit être 'Zone_1' ou 'Zone_2'")
        return v

    @model_validator(mode="before")
    @classmethod
    def handle_zone_alias(cls, data: Any) -> Any:
        if isinstance(data, dict):
            zone = data.get("zone_id") or data.get("current_zone")
            if zone is not None:
                data["zone_id"] = zone
        return data


class StreamSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    session_id: str
    owner_email: str
    node_id: Optional[str] = None
    zone_id: Optional[str] = None
    current_zone: str = "Zone_1"
    status: str = "live"


UserSession = StreamSessionOut

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class RunInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    theme: str = Field(min_length=1, max_length=200)
    budget: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    deadline: datetime | None = None

    @field_validator("theme")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("theme must not be blank")
        return value.strip()

    @field_validator("deadline")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("deadline requires a timezone, e.g. +08:00")
        return value


class Empty(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Search(Empty):
    max_license_cost: Decimal | None = Field(default=None, ge=0, allow_inf_nan=False)


class Revise(Empty):
    material_id: str
    objective: str = Field(min_length=1, max_length=1000)
    channel: str = Field(min_length=1, max_length=100)


class Script(Empty):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=20, max_length=12000)
    material_ids: list[str] = Field(min_length=1)


class Stop(Empty):
    reason: str = Field(min_length=1, max_length=1000)


class Action(Empty):
    name: Literal["search_materials", "revise_plan", "estimate_plan", "generate_script", "save_plan", "finish", "stop"]
    arguments: dict
    reason_summary: str = Field(min_length=1, max_length=1000)


PARAMS = {
    "search_materials": Search, "revise_plan": Revise, "estimate_plan": Empty,
    "generate_script": Script, "save_plan": Empty, "finish": Empty, "stop": Stop,
}

DESCRIPTIONS = {
    "search_materials": "Search synthetic materials; partial coverage is explicitly reported. Cost 0.",
    "revise_plan": "Select a retrieved material and revise objective/channel; dependent outputs become superseded.",
    "estimate_plan": "Calculate authoritative cumulative completion cost and remaining duration for current plan.",
    "generate_script": "Validate and persist your original script referencing selected materials. Requires feasible estimate; charges on success.",
    "save_plan": "Atomically save current plan and settle remaining fees. Requires script and fresh feasible estimate.",
    "finish": "Finish ONLY after a saved plan exists. Tool checks completion conditions.",
    "stop": "Stop with an honest incomplete reason if no feasible path remains.",
}


def tool_schemas():
    result = []
    for name, schema in PARAMS.items():
        parameters = schema.model_json_schema()
        parameters["properties"]["reason_summary"] = {
            "type": "string", "description": "Brief decision basis, not private chain of thought."
        }
        parameters.setdefault("required", []).append("reason_summary")
        result.append({"type": "function", "function": {
            "name": name, "description": DESCRIPTIONS[name], "parameters": parameters,
        }})
    return result

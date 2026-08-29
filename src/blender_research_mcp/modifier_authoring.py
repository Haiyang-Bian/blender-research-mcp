"""Closed-world schemas for bounded Modifier stack authoring."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from blender_research_mcp.authoring import FiniteNumber

ModifierType = Literal["BEVEL", "SUBSURF", "SOLIDIFY", "BOOLEAN"]
ModifierStackIndex = Annotated[StrictInt, Field(ge=0, le=255)]


class BooleanOperand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: str = Field(min_length=1, max_length=255)
    expected_object_identity: str = Field(min_length=1, max_length=128)


class _ModifierDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    stack_index: ModifierStackIndex | None = None
    show_viewport: StrictBool = True
    show_render: StrictBool = True


class BevelDefinition(_ModifierDefinition):
    type: Literal["BEVEL"]
    width: FiniteNumber = Field(default=0.1, ge=0, le=100_000)
    segments: Annotated[StrictInt, Field(ge=1, le=64)] = 2
    limit_method: Literal["NONE", "ANGLE"] = "ANGLE"
    angle_limit_degrees: FiniteNumber = Field(default=30.0, ge=0, le=180)
    affect: Literal["EDGES", "VERTICES"] = "EDGES"
    width_mode: Literal["OFFSET", "WIDTH", "DEPTH", "ABSOLUTE"] = "OFFSET"
    profile: FiniteNumber = Field(default=0.5, ge=0, le=1)
    clamp_overlap: StrictBool = True
    harden_normals: StrictBool = False


class SubdivisionDefinition(_ModifierDefinition):
    type: Literal["SUBSURF"]
    subdivision_type: Literal["CATMULL_CLARK", "SIMPLE"] = "CATMULL_CLARK"
    levels: Annotated[StrictInt, Field(ge=0, le=4)] = 2
    render_levels: Annotated[StrictInt, Field(ge=0, le=4)] = 2
    quality: Annotated[StrictInt, Field(ge=1, le=6)] = 3
    show_only_control_edges: StrictBool = False
    use_limit_surface: StrictBool = True
    use_creases: StrictBool = True


class SolidifyDefinition(_ModifierDefinition):
    type: Literal["SOLIDIFY"]
    thickness: FiniteNumber = Field(default=0.01, ge=-100_000, le=100_000)
    offset: FiniteNumber = Field(default=-1.0, ge=-1, le=1)
    use_even_offset: StrictBool = False
    use_quality_normals: StrictBool = False
    use_rim: StrictBool = True
    use_rim_only: StrictBool = False
    use_flip_normals: StrictBool = False

    @model_validator(mode="after")
    def validate_rim(self) -> SolidifyDefinition:
        if self.use_rim_only and not self.use_rim:
            raise ValueError("use_rim_only=true requires use_rim=true")
        return self


class BooleanDefinition(_ModifierDefinition):
    type: Literal["BOOLEAN"]
    operation: Literal["DIFFERENCE", "UNION", "INTERSECT"] = "DIFFERENCE"
    solver: Literal["FAST", "EXACT"] = "EXACT"
    operand: BooleanOperand
    use_self: StrictBool = False
    use_hole_tolerant: StrictBool = False
    double_threshold: FiniteNumber | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def validate_solver_options(self) -> BooleanDefinition:
        if self.solver == "FAST" and (self.use_self or self.use_hole_tolerant):
            raise ValueError("use_self and use_hole_tolerant require solver=EXACT")
        if self.solver == "EXACT" and self.double_threshold is not None:
            raise ValueError("double_threshold is only available with solver=FAST")
        return self


ModifierDefinition = Annotated[
    BevelDefinition | SubdivisionDefinition | SolidifyDefinition | BooleanDefinition,
    Field(discriminator="type"),
]


class _ModifierSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    show_viewport: StrictBool | None = None
    show_render: StrictBool | None = None

    @model_validator(mode="after")
    def require_setting(self) -> _ModifierSettings:
        if not self.model_dump(exclude_none=True, exclude={"type"}):
            raise ValueError("settings must contain at least one field")
        return self


class BevelSettings(_ModifierSettings):
    type: Literal["BEVEL"]
    width: FiniteNumber | None = Field(default=None, ge=0, le=100_000)
    segments: Annotated[StrictInt, Field(ge=1, le=64)] | None = None
    limit_method: Literal["NONE", "ANGLE"] | None = None
    angle_limit_degrees: FiniteNumber | None = Field(default=None, ge=0, le=180)
    affect: Literal["EDGES", "VERTICES"] | None = None
    width_mode: Literal["OFFSET", "WIDTH", "DEPTH", "ABSOLUTE"] | None = None
    profile: FiniteNumber | None = Field(default=None, ge=0, le=1)
    clamp_overlap: StrictBool | None = None
    harden_normals: StrictBool | None = None


class SubdivisionSettings(_ModifierSettings):
    type: Literal["SUBSURF"]
    subdivision_type: Literal["CATMULL_CLARK", "SIMPLE"] | None = None
    levels: Annotated[StrictInt, Field(ge=0, le=4)] | None = None
    render_levels: Annotated[StrictInt, Field(ge=0, le=4)] | None = None
    quality: Annotated[StrictInt, Field(ge=1, le=6)] | None = None
    show_only_control_edges: StrictBool | None = None
    use_limit_surface: StrictBool | None = None
    use_creases: StrictBool | None = None


class SolidifySettings(_ModifierSettings):
    type: Literal["SOLIDIFY"]
    thickness: FiniteNumber | None = Field(default=None, ge=-100_000, le=100_000)
    offset: FiniteNumber | None = Field(default=None, ge=-1, le=1)
    use_even_offset: StrictBool | None = None
    use_quality_normals: StrictBool | None = None
    use_rim: StrictBool | None = None
    use_rim_only: StrictBool | None = None
    use_flip_normals: StrictBool | None = None


class BooleanSettings(_ModifierSettings):
    type: Literal["BOOLEAN"]
    operation: Literal["DIFFERENCE", "UNION", "INTERSECT"] | None = None
    solver: Literal["FAST", "EXACT"] | None = None
    operand: BooleanOperand | None = None
    use_self: StrictBool | None = None
    use_hole_tolerant: StrictBool | None = None
    double_threshold: FiniteNumber | None = Field(default=None, ge=0, le=1)


ModifierSettings = Annotated[
    BevelSettings | SubdivisionSettings | SolidifySettings | BooleanSettings,
    Field(discriminator="type"),
]

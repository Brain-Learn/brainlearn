"""Versioned, JSON-serializable workflow graph schema."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScientificType(StrEnum):
    BIDS_DATASET = "bids_dataset"
    RAW_EEG = "raw_eeg"
    REVIEWED_EEG = "reviewed_eeg"
    EPOCHS = "epochs"
    EVOKED = "evoked"
    SPECTRUM = "spectrum"
    REPORT = "report"


class PortDirection(StrEnum):
    INPUT = "input"
    OUTPUT = "output"


class PortDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    direction: PortDirection
    data_type: ScientificType
    required: bool = True


class ParameterDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: Any = None
    required: bool = False
    description: str | None = None


class ParameterSchema(BaseModel):
    """UI-independent description of one editable node parameter."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value_type: Literal["string", "number", "integer", "boolean"]
    default: Any = None
    required: bool = False
    description: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    options: list[str] | None = None


class CitationMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1)
    doi: str | None = None
    url: str | None = None


class LicenseMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    spdx_id: str | None = None
    url: str | None = None


class CapabilityRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    required: bool = True
    description: str = Field(min_length=1)


class NodeManifest(BaseModel):
    """Versioned registry contract; it describes nodes but does not execute them."""

    model_config = ConfigDict(extra="forbid")

    manifest_schema_version: Literal["1.0"] = "1.0"
    id: str = Field(min_length=1)
    node_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    label: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1)
    status: Literal["example", "experimental", "certified"] = "example"
    ports: list[PortDefinition] = Field(default_factory=list)
    parameters: list[ParameterSchema] = Field(default_factory=list)
    review_behavior: Literal["none", "required"] = "none"
    citations: list[CitationMetadata] = Field(default_factory=list)
    license: LicenseMetadata
    capability_requirements: list[CapabilityRequirement] = Field(default_factory=list)


class CanvasPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


PRESENTATION_ACCENTS: tuple[str, ...] = ("teal", "blue", "violet", "amber", "rose", "slate")
PRESENTATION_TITLE_MAX_LENGTH = 80
PRESENTATION_NOTES_MAX_LENGTH = 2000


class NodePresentation(BaseModel):
    """Display-only per-instance customization for one node.

    Presentation never affects computation: it is excluded from workflow
    and node content identities by construction, so title, accent,
    compactness, and notes changes never invalidate cached work. Absent
    presentation (the form of every pre-4E.2 workflow) means manifest
    defaults: the manifest label, the default accent, expanded display,
    and no notes.
    """

    model_config = ConfigDict(extra="forbid")

    title: str | None = Field(default=None, min_length=1, max_length=PRESENTATION_TITLE_MAX_LENGTH)
    accent: Literal["teal", "blue", "violet", "amber", "rose", "slate"] = "teal"
    compact: bool = False
    notes: str = Field(default="", max_length=PRESENTATION_NOTES_MAX_LENGTH)

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: Any) -> Any:
        if value is None or not isinstance(value, str):
            return value
        trimmed = value.strip()
        if trimmed == "":
            raise ValueError("Node presentation title must contain a non-whitespace character.")
        return trimmed


class NodeInstance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    type: str = Field(min_length=1)
    label: str = Field(min_length=1)
    category: str = Field(min_length=1)
    description: str | None = None
    position: CanvasPosition
    ports: list[PortDefinition] = Field(default_factory=list)
    parameters: list[ParameterDefinition] = Field(default_factory=list)
    pauses_for_review: bool = False
    presentation: NodePresentation | None = None


class EdgeEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1)
    port_id: str = Field(min_length=1)


class Edge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: EdgeEndpoint
    target: EdgeEndpoint


class GraphMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""
    created_with: str = "BrainLearn"
    modality: Literal["EEG"] = "EEG"
    status: Literal["example", "experimental", "certified"] = "example"


class Workflow(BaseModel):
    """Canonical persisted workflow format for the initial schema version."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(min_length=1)
    metadata: GraphMetadata
    nodes: list[NodeInstance]
    edges: list[Edge]

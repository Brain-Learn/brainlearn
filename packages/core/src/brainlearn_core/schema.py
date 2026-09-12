"""Versioned, JSON-serializable workflow graph schema."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class CanvasPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


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

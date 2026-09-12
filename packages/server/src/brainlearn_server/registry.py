"""Built-in non-executing node manifests for the editable graph prototype."""

from brainlearn_core import (
    CanvasPosition,
    LicenseMetadata,
    NodeInstance,
    NodeManifest,
    ParameterDefinition,
    ParameterSchema,
    PortDefinition,
    PortDirection,
    ScientificType,
)

EXAMPLE_LICENSE = LicenseMetadata(
    name="BSD 3-Clause License",
    spdx_id="BSD-3-Clause",
    url="https://opensource.org/license/bsd-3-clause",
)


def _port(
    identifier: str,
    label: str,
    direction: PortDirection,
    data_type: ScientificType,
) -> PortDefinition:
    return PortDefinition(
        id=identifier,
        label=label,
        direction=direction,
        data_type=data_type,
    )


NODE_REGISTRY: tuple[NodeManifest, ...] = (
    NodeManifest(
        id="input.bids_eeg",
        node_version="0.1.0",
        label="BIDS EEG",
        description="Choose a BIDS-compatible EEG dataset.",
        category="Input",
        ports=[_port("dataset", "Dataset", PortDirection.OUTPUT, ScientificType.BIDS_DATASET)],
        parameters=[
            ParameterSchema(
                id="root",
                label="Dataset root",
                value_type="string",
                default="synthetic/example-bids",
                required=True,
                description="A project-relative dataset path. File access is not implemented yet.",
            )
        ],
        license=EXAMPLE_LICENSE,
    ),
    NodeManifest(
        id="eeg.inspect",
        node_version="0.1.0",
        label="Inspect Signal",
        description="Review raw traces and acquisition metadata.",
        category="Quality control",
        ports=[
            _port("dataset", "Dataset", PortDirection.INPUT, ScientificType.BIDS_DATASET),
            _port("raw", "Raw EEG", PortDirection.OUTPUT, ScientificType.RAW_EEG),
        ],
        review_behavior="required",
        license=EXAMPLE_LICENSE,
    ),
    NodeManifest(
        id="eeg.filter",
        node_version="0.1.0",
        label="Band-pass Filter",
        description="Configure a visible, provenance-tracked frequency filter.",
        category="Preprocessing",
        ports=[
            _port("raw", "Raw EEG", PortDirection.INPUT, ScientificType.RAW_EEG),
            _port("filtered", "Filtered EEG", PortDirection.OUTPUT, ScientificType.RAW_EEG),
        ],
        parameters=[
            ParameterSchema(
                id="low_hz",
                label="Low cutoff (Hz)",
                value_type="number",
                default=1.0,
                required=True,
                minimum=0.0,
            ),
            ParameterSchema(
                id="high_hz",
                label="High cutoff (Hz)",
                value_type="number",
                default=40.0,
                required=True,
                minimum=0.0,
            ),
        ],
        license=EXAMPLE_LICENSE,
    ),
    NodeManifest(
        id="eeg.ica_review",
        node_version="0.1.0",
        label="ICA Review",
        description="Pause for a researcher to review proposed ICA components.",
        category="Quality control",
        ports=[
            _port("raw", "Raw EEG", PortDirection.INPUT, ScientificType.RAW_EEG),
            _port("reviewed", "Reviewed EEG", PortDirection.OUTPUT, ScientificType.REVIEWED_EEG),
        ],
        review_behavior="required",
        license=EXAMPLE_LICENSE,
    ),
    NodeManifest(
        id="eeg.epochs",
        node_version="0.1.0",
        label="Epochs",
        description="Create event-locked epochs using explicit time bounds.",
        category="Processing",
        ports=[
            _port("reviewed", "Reviewed EEG", PortDirection.INPUT, ScientificType.REVIEWED_EEG),
            _port("epochs", "Epochs", PortDirection.OUTPUT, ScientificType.EPOCHS),
        ],
        parameters=[
            ParameterSchema(
                id="event",
                label="Event name",
                value_type="string",
                default="stimulus",
                required=True,
            ),
            ParameterSchema(
                id="tmin",
                label="Start (s)",
                value_type="number",
                default=-0.2,
                required=True,
            ),
            ParameterSchema(
                id="tmax",
                label="End (s)",
                value_type="number",
                default=0.8,
                required=True,
            ),
        ],
        license=EXAMPLE_LICENSE,
    ),
    NodeManifest(
        id="eeg.erp_average",
        node_version="0.1.0",
        label="ERP Average",
        description="Average selected epochs to form an evoked response.",
        category="Analysis",
        ports=[
            _port("epochs", "Epochs", PortDirection.INPUT, ScientificType.EPOCHS),
            _port("evoked", "Evoked", PortDirection.OUTPUT, ScientificType.EVOKED),
        ],
        license=EXAMPLE_LICENSE,
    ),
    NodeManifest(
        id="eeg.psd",
        node_version="0.1.0",
        label="PSD",
        description="Estimate a power spectrum from the reviewed epochs.",
        category="Analysis",
        ports=[
            _port("epochs", "Epochs", PortDirection.INPUT, ScientificType.EPOCHS),
            _port("spectrum", "Spectrum", PortDirection.OUTPUT, ScientificType.SPECTRUM),
        ],
        license=EXAMPLE_LICENSE,
    ),
    NodeManifest(
        id="output.report",
        node_version="0.1.0",
        label="Report",
        description="Combine analysis artifacts into a future methods and results report.",
        category="Output",
        ports=[
            _port("evoked", "Evoked", PortDirection.INPUT, ScientificType.EVOKED),
            _port("spectrum", "Spectrum", PortDirection.INPUT, ScientificType.SPECTRUM),
            _port("report", "Report", PortDirection.OUTPUT, ScientificType.REPORT),
        ],
        license=EXAMPLE_LICENSE,
    ),
)

NODE_REGISTRY_BY_ID = {manifest.id: manifest for manifest in NODE_REGISTRY}


def list_node_manifests() -> list[NodeManifest]:
    return list(NODE_REGISTRY)


def get_node_manifest(node_type: str) -> NodeManifest | None:
    return NODE_REGISTRY_BY_ID.get(node_type)


def instantiate_registered_node(
    node_type: str, instance_id: str, position: CanvasPosition
) -> NodeInstance:
    """Create a non-executing workflow instance from the canonical manifest."""

    manifest = get_node_manifest(node_type)
    if manifest is None:
        raise ValueError(f"Node manifest '{node_type}' was not found.")
    return NodeInstance(
        id=instance_id,
        type=manifest.id,
        label=manifest.label,
        category=manifest.category,
        description=manifest.description,
        position=position,
        ports=manifest.ports,
        parameters=[
            ParameterDefinition(
                id=parameter.id,
                label=parameter.label,
                value=parameter.default,
                required=parameter.required,
                description=parameter.description,
            )
            for parameter in manifest.parameters
        ],
        pauses_for_review=manifest.review_behavior == "required",
    )

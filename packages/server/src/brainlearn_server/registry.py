"""Node manifests for the editable graph and its allowlisted worker.

The curated BIDS EEG input, read-only inspection, and experimental filter
execute through explicit worker adapters. Later EEG operations remain
illustrative. The ``demo.*`` manifests are explicitly non-scientific
demonstration nodes whose
ports and versions derive from the authoritative worker contract in
:mod:`brainlearn_server.demo_nodes`.
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as installed_distribution_version
from typing import Literal

from brainlearn_core import (
    CanvasPosition,
    CitationMetadata,
    CodeRendererMetadata,
    LicenseMetadata,
    NodeInstance,
    NodeManifest,
    ParameterDefinition,
    ParameterSchema,
    PortDefinition,
    PortDirection,
    ScientificType,
    SoftwareDependencyMetadata,
)

from brainlearn_server.demo_nodes import DEMO_NODES
from brainlearn_server.python_export import (
    CODE_RENDERER_ID,
    CODE_RENDERER_VERSION,
    ICA_CODE_RENDERER_ID,
    ICA_CODE_RENDERER_VERSION,
)

EXAMPLE_LICENSE = LicenseMetadata(
    name="BSD 3-Clause License",
    spdx_id="BSD-3-Clause",
    url="https://opensource.org/license/bsd-3-clause",
)


_UPSTREAM_LICENSE = LicenseMetadata(
    name="BSD 3-Clause License",
    spdx_id="BSD-3-Clause",
    url="https://opensource.org/license/bsd-3-clause",
)


def software_dependency_metadata(
    package_name: str,
    version: str,
    license: LicenseMetadata,
    citations: list[CitationMetadata],
) -> SoftwareDependencyMetadata:
    """Describe a pinned dependency and snapshot its installed distribution version.

    Reading distribution metadata does not import the optional scientific package
    or validate that its runtime dependencies and backends work.
    """

    installation_status: Literal["installed", "missing", "version_mismatch"]
    try:
        installed_version = installed_distribution_version(package_name)
    except PackageNotFoundError:
        installed_version = None
        installation_status = "missing"
    else:
        installation_status = "installed" if installed_version == version else "version_mismatch"
    return SoftwareDependencyMetadata(
        package_name=package_name,
        version=version,
        license=license,
        citations=citations,
        installation_status=installation_status,
        installed_version=installed_version,
    )


_MNE_DEPENDENCY = software_dependency_metadata(
    package_name="mne",
    version="1.13.2",
    license=_UPSTREAM_LICENSE,
    citations=[
        CitationMetadata(
            title="MEG and EEG data analysis with MNE-Python",
            doi="10.3389/fnins.2013.00267",
            url="https://doi.org/10.3389/fnins.2013.00267",
        ),
        CitationMetadata(
            title="MNE-Python software archive",
            doi="10.5281/zenodo.592483",
            url="https://doi.org/10.5281/zenodo.592483",
        ),
    ],
)

_MNE_BIDS_DEPENDENCY = software_dependency_metadata(
    package_name="mne-bids",
    version="0.20.0",
    license=_UPSTREAM_LICENSE,
    citations=[
        CitationMetadata(
            title=(
                "MNE-BIDS: Organizing electrophysiological data into the BIDS format "
                "and facilitating their analysis"
            ),
            doi="10.21105/joss.01896",
            url="https://doi.org/10.21105/joss.01896",
        ),
    ],
)

_SCIKIT_LEARN_DEPENDENCY = software_dependency_metadata(
    package_name="scikit-learn",
    version="1.7.2",
    license=LicenseMetadata(
        name="BSD 3-Clause License",
        spdx_id="BSD-3-Clause",
        url="https://opensource.org/license/bsd-3-clause",
    ),
    citations=[
        CitationMetadata(
            title="Scikit-learn: Machine Learning in Python",
            doi="10.48550/arXiv.1201.0490",
            url="https://scikit-learn.org/1.7/",
        )
    ],
)


def _software_dependencies(
    *dependencies: SoftwareDependencyMetadata,
) -> list[SoftwareDependencyMetadata]:
    return [dependency.model_copy(deep=True) for dependency in dependencies]


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


_EEG_MANIFESTS: tuple[NodeManifest, ...] = (
    NodeManifest(
        id="input.bids_eeg",
        node_version="0.2.0",
        label="BIDS EEG",
        description="Use a discovered project-local BIDS EEG dataset as a workflow input.",
        category="Input",
        status="experimental",
        ports=[_port("dataset", "Dataset", PortDirection.OUTPUT, ScientificType.BIDS_DATASET)],
        parameters=[
            ParameterSchema(
                id="root",
                label="Dataset root",
                value_type="string",
                default="synthetic/example-bids",
                required=True,
                description="Project-relative path to a discovered BIDS EEG dataset.",
            )
        ],
        license=EXAMPLE_LICENSE,
        software_dependencies=_software_dependencies(_MNE_DEPENDENCY, _MNE_BIDS_DEPENDENCY),
    ),
    NodeManifest(
        id="eeg.inspect",
        node_version="0.2.0",
        label="Inspect Signal",
        description=(
            "Read measured signal and acquisition properties without transforming the "
            "recording or generating previews."
        ),
        category="Quality control",
        status="experimental",
        ports=[
            _port("dataset", "Dataset", PortDirection.INPUT, ScientificType.BIDS_DATASET),
            _port("raw", "Raw EEG reference", PortDirection.OUTPUT, ScientificType.RAW_EEG),
            _port("report", "Inspection report", PortDirection.OUTPUT, ScientificType.REPORT),
        ],
        parameters=[
            ParameterSchema(
                id="recording_path",
                label="Recording path",
                value_type="string",
                default="",
                required=True,
                description="Path to one discovered recording, relative to its dataset root.",
            )
        ],
        license=EXAMPLE_LICENSE,
        software_dependencies=_software_dependencies(_MNE_DEPENDENCY, _MNE_BIDS_DEPENDENCY),
    ),
    NodeManifest(
        id="eeg.bad_channel_review",
        node_version="0.1.0",
        label="Bad Channel Review",
        description=(
            "Pause for a researcher to record EEG channels for exclusion. The decision is saved "
            "with run, node, input, and source identities; the source recording is not modified."
        ),
        category="Quality control",
        status="experimental",
        ports=[
            _port("raw", "Raw EEG", PortDirection.INPUT, ScientificType.RAW_EEG),
        ],
        review_behavior="required",
        license=EXAMPLE_LICENSE,
        software_dependencies=_software_dependencies(_MNE_DEPENDENCY, _MNE_BIDS_DEPENDENCY),
    ),
    NodeManifest(
        id="eeg.filter",
        node_version="1.1.0",
        label="Band-pass Filter",
        description=(
            "Apply an MNE FIR band-pass and optional notch filter to EEG channels, with a "
            "bounded before/after preview and provenance."
        ),
        category="Preprocessing",
        status="experimental",
        ports=[
            _port("raw", "Raw EEG", PortDirection.INPUT, ScientificType.RAW_EEG),
            _port("filtered", "Filtered EEG", PortDirection.OUTPUT, ScientificType.RAW_EEG),
            _port("preview", "Before/after preview", PortDirection.OUTPUT, ScientificType.REPORT),
            _port("provenance", "Filter provenance", PortDirection.OUTPUT, ScientificType.REPORT),
        ],
        parameters=[
            ParameterSchema(
                id="low_hz",
                label="Low cutoff (Hz)",
                value_type="number",
                default=1.0,
                required=True,
                description="High-pass cutoff in Hz; must be lower than the high cutoff.",
                minimum=0.1,
                maximum=100.0,
            ),
            ParameterSchema(
                id="high_hz",
                label="High cutoff (Hz)",
                value_type="number",
                default=40.0,
                required=True,
                description=(
                    "Low-pass cutoff in Hz; must be below the recording's Nyquist frequency."
                ),
                minimum=0.5,
                maximum=200.0,
            ),
            ParameterSchema(
                id="notch_hz",
                label="Notch frequencies (Hz)",
                value_type="string",
                default="",
                description=(
                    "Optional comma-separated notch centers in Hz; leave blank to disable. "
                    "Each center must fall within the pass band and below Nyquist."
                ),
            ),
            ParameterSchema(
                id="notch_width_hz",
                label="Notch width (Hz)",
                value_type="number",
                default=1.0,
                description="Width passed to MNE's FIR notch filter, in Hz.",
                minimum=0.1,
                maximum=10.0,
            ),
        ],
        license=EXAMPLE_LICENSE,
        software_dependencies=_software_dependencies(_MNE_DEPENDENCY, _MNE_BIDS_DEPENDENCY),
        code_renderers=[
            CodeRendererMetadata(
                id=CODE_RENDERER_ID,
                version=CODE_RENDERER_VERSION,
                language="python",
                description=(
                    "Exports the supported BIDS import, inspection, and MNE filter path as a "
                    "standalone Python script."
                ),
            )
        ],
    ),
    NodeManifest(
        id="eeg.ica_review",
        node_version="0.1.0",
        label="ICA Component Review",
        description=(
            "Pause for explicit researcher component choices, then apply the saved exclusions "
            "to the matching input recording."
        ),
        category="Quality control",
        status="experimental",
        ports=[
            _port("raw", "Raw EEG", PortDirection.INPUT, ScientificType.RAW_EEG),
            _port("fit", "Fitted ICA", PortDirection.INPUT, ScientificType.ICA_FIT),
            _port("reviewed", "Reviewed EEG", PortDirection.OUTPUT, ScientificType.REVIEWED_EEG),
            _port("provenance", "Review provenance", PortDirection.OUTPUT, ScientificType.REPORT),
        ],
        review_behavior="required",
        license=EXAMPLE_LICENSE,
        software_dependencies=_software_dependencies(
            _MNE_DEPENDENCY, _MNE_BIDS_DEPENDENCY, _SCIKIT_LEARN_DEPENDENCY
        ),
        code_renderers=[
            CodeRendererMetadata(
                id=ICA_CODE_RENDERER_ID,
                version=ICA_CODE_RENDERER_VERSION,
                language="python",
                description=(
                    "Exports the supported EEG filter and ICA path with a persisted approved "
                    "component decision."
                ),
            )
        ],
    ),
    NodeManifest(
        id="eeg.ica_fit",
        node_version="0.1.0",
        label="Fit ICA",
        description=(
            "Fit an MNE ICA model and inspect component maps, source time courses, spectra, "
            "and diagnostics without rejecting components."
        ),
        category="Preprocessing",
        status="experimental",
        ports=[
            _port("raw", "Raw EEG", PortDirection.INPUT, ScientificType.RAW_EEG),
            _port("ica", "Fitted ICA model", PortDirection.OUTPUT, ScientificType.ICA_FIT),
            _port("diagnostics", "ICA components", PortDirection.OUTPUT, ScientificType.REPORT),
            _port("provenance", "Fit provenance", PortDirection.OUTPUT, ScientificType.REPORT),
        ],
        license=EXAMPLE_LICENSE,
        software_dependencies=_software_dependencies(
            _MNE_DEPENDENCY, _MNE_BIDS_DEPENDENCY, _SCIKIT_LEARN_DEPENDENCY
        ),
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
        software_dependencies=_software_dependencies(_MNE_DEPENDENCY),
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
        software_dependencies=_software_dependencies(_MNE_DEPENDENCY),
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
        software_dependencies=_software_dependencies(_MNE_DEPENDENCY),
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

DEMO_PORT_TYPE = ScientificType.RAW_EEG

_DEMO_LABELS: dict[str, tuple[str, str]] = {
    "demo.delay": (
        "Delay",
        "Non-scientific demonstration node: waits cooperatively, then completes "
        "without outputs. Use it for cancellation exercises.",
    ),
    "demo.copy": (
        "Copy",
        "Non-scientific demonstration node: writes canned text to one output "
        "file. Produces no scientific result.",
    ),
    "demo.fail": (
        "Fail",
        "Non-scientific demonstration node: fails on purpose with a controlled "
        "message. Use it for failure-propagation exercises.",
    ),
    "demo.review": (
        "Review",
        "Non-scientific demonstration node: pauses for an explicit researcher "
        "decision. Use it for review approval/rejection exercises.",
    ),
    "demo.relay": (
        "Relay",
        "Non-scientific demonstration node: forwards one input's bytes to one "
        "output. Produces no scientific result.",
    ),
}

_DEMO_PORT_LABELS = {"in": "Input", "out": "Output", "output": "Output"}


def _demo_parameter_schemas(node_type: str) -> list[ParameterSchema]:
    """Derive UI parameter schemas from the worker parameter contract."""

    return [
        ParameterSchema(
            id=spec.id,
            label=spec.label,
            value_type=spec.value_type,
            default=spec.default,
            required=spec.required,
            description=spec.description,
            minimum=spec.minimum,
            maximum=spec.maximum,
        )
        for spec in DEMO_NODES[node_type].parameters.values()
    ]


def demo_manifest(node_type: str) -> NodeManifest:
    """Build the registry manifest for one worker demonstration node.

    Ports and the implementation version come from the authoritative worker
    contract, so the registry cannot drift from what execution accepts. Only
    display metadata (labels, descriptions, parameter schemas) is authored
    here, and every description states the node is non-scientific.
    """

    worker = DEMO_NODES[node_type]
    label, description = _DEMO_LABELS[node_type]
    return NodeManifest(
        id=node_type,
        node_version=worker.version,
        label=label,
        description=description,
        category="Demonstration",
        ports=[
            PortDefinition(
                id=port_id,
                label=_DEMO_PORT_LABELS.get(port_id, port_id),
                direction=PortDirection.INPUT,
                data_type=DEMO_PORT_TYPE,
                required=required,
            )
            for port_id, required in worker.inputs.items()
        ]
        + [
            PortDefinition(
                id=port_id,
                label=_DEMO_PORT_LABELS.get(port_id, port_id),
                direction=PortDirection.OUTPUT,
                data_type=DEMO_PORT_TYPE,
                required=required,
            )
            for port_id, required in worker.outputs.items()
        ],
        parameters=_demo_parameter_schemas(node_type),
        review_behavior="required" if node_type == "demo.review" else "none",
        license=EXAMPLE_LICENSE,
    )


DEMO_MANIFESTS: tuple[NodeManifest, ...] = tuple(
    demo_manifest(node_type) for node_type in DEMO_NODES
)

NODE_REGISTRY: tuple[NodeManifest, ...] = _EEG_MANIFESTS + DEMO_MANIFESTS

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

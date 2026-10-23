# Workflow, service, and plugin contracts

These contracts help contributors connect components without depending on internal implementation modules.

| Boundary | Current contract | Stability |
| --- | --- | --- |
| Python workflow definitions | `brainlearn_core` public exports and versioned Pydantic models | Maintained public Python API |
| Workflow JSON | Versioned schema, scientific port types, parameter definitions, and explicit edges | Versioned; migrations are part of the core |
| Local service | FastAPI endpoints for health, capabilities, registry, validation, projects, and runs | Application contract; endpoint details are being documented |
| EEG adapters | Registry manifests and worker contracts for BIDS import, inspection, filtering, and QC review | Experimental; MNE-Python/MNE-BIDS own scientific algorithms |
| React interface | Registry-driven graph and API client | Product interface; not a supported package API |

Source references:

- [Core schema and validator](https://github.com/Brain-Learn/brainlearn/tree/main/packages/core/src/brainlearn_core)
- [Local API](https://github.com/Brain-Learn/brainlearn/tree/main/packages/server/src/brainlearn_server)
- [MNE adapters](https://github.com/Brain-Learn/brainlearn/tree/main/plugins/mne/src/brainlearn_mne)
- [Frontend application](https://github.com/Brain-Learn/brainlearn/tree/main/apps/web/src)
- [Execution and dataset contracts](https://github.com/Brain-Learn/brainlearn/tree/main/docs)

Do not build external extensions against an undocumented internal class. A contract becomes supported only after it has a version, migration policy, tests, and maintainer ownership.

# BrainLearn

BrainLearn is a planned local-first visual workflow builder for reproducible neuroscience analysis. The first target is a complete EEG workflow with typed connections, explicit quality-control decisions, provenance, and reproducible export.

The project now has an executable foundation: a versioned workflow schema and validator, a loopback FastAPI service, system-capability reporting, and a React Flow interface that renders and inspects the example EEG graph. Scientific processing is not implemented yet.

- Read the [full proposal](PROPOSAL.md).
- Read the [agent and contributor handoff](AGENTS.md).
- Follow the [local development guide](docs/development.md).
- View the [initial interface concept](design/brainlearn-layout-concept-v1.png).

BrainLearn is intended for research use and is not a medical device or diagnostic system.

BrainLearn's original code is licensed under the [BSD 3-Clause License](LICENSE). Third-party libraries, scientific tools, datasets, atlases, models, and plugins remain subject to their own licenses.

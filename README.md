# Data Quality Checker — Worklet

An AI tool that checks training data (initially images with bounding boxes, segmentation masks and labels) against reviewer-confirmed rules extracted from plain text guidelines. The initial prototype runs locally and offline on a CPU using free/open-source models and tools.

- **[SRS.md](SRS.md):** the latest mentor clarifications and project context. These take precedence over earlier proposals and assumptions.
- **[RULES.md](RULES.md):** the Penn-Fudan development dataset and example rulebook. Sample data and rules are examples, not the complete, fixed product specification.

## Project structure

- **[backend/README.md](backend/README.md):** Python package scaffold. Implementation starts here, with the Penn-Fudan dataset reader.
- **[frontend/README.md](frontend/README.md):** reserved for the interface.

The user is writing the application. The scaffold contains folders and responsibility notes, with no application logic. The existing virtual environment, dependency list and dataset stay at the project root.

## Guideline input

Plain text guidelines are the primary requirement. Start with an editable text area where the user can type or paste them. Support for guideline documents, screenshots, scanned images and other formats is secondary and can be added later.

The tool must interpret the supplied guidelines and use the reviewer-confirmed rulebook. Dataset-specific checks must not silently replace the user's guidelines with a fixed Penn-Fudan rule set.

## What the tool does

1. **Rules:** reads the plain text guideline, extracts the rules and cites the source sentence for each one. A reviewer confirms or edits the list and can add rules by hand.
2. **Check:** checks every uploaded image and its annotation against the confirmed rules.
3. **Review:** shows flagged, uncertain and apparently correct data, with the check results and evidence. The reviewer controls final decisions, accepts or rejects findings, and can correct actual annotation errors. Confidence alone does not automatically accept or reject data.
4. **Improve:** records corrections and feedback and uses them to improve future audits. Implement and evaluate the improvement mechanism; storing a review history alone is insufficient.

Every finding, reviewer decision and correction is logged with its evidence and the rule version used.

## Environment

- **CPU prototype first.** Use free/open-source models and tools. Evaluate the prototype before a later GPU implementation. The intended deployment hardware and operating system have not been confirmed by the mentors.
- **No cloud AI services.** Local models only (for example through Ollama), and no data leaves the machine.
- **Batch size:** determine it from measured CPU performance and available resources. The 170-image Penn-Fudan set is a local development example.
- **Deadline:** the user stated 31 October. Prioritize plain text guideline input and a complete audit, review and correction workflow.

## Measuring success

- Measure finding precision and recall against reviewer-verified examples, including missed errors and false alarms.
- Every guideline-based finding should cite its confirmed source rule. Identify manually authored rules explicitly.
- Verify that the reviewer can inspect apparently correct data, accept or reject findings, and correct annotations.
- Measure CPU processing time and memory use before selecting a batch size.
- Evaluate whether using reviewer feedback improves future audits on separate examples.

The earlier target of at least 95% correct automatic decisions is superseded by the mentors' requirement for reviewer-controlled final decisions.

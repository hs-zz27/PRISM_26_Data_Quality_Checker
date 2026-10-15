# Software Requirements Specification (SRS)

Recorded on 13 October 2026 from the mentor reply and WhatsApp screenshot supplied by the user. This file preserves the current requirements for future work. New mentor clarifications take precedence over older proposals, meeting interpretations and development assumptions.

## Guideline input priority

The screenshot contains this clarification:

> Please prioritize providing guidelines as plain text first. This is the primary requirement. Supporting other input methods, such as documents, images, or any other formats, is secondary and can be implemented later.

- Implement plain text guideline entry first.
- The initial interface can provide an editable text area for typing or pasting guidelines. This is our implementation choice for meeting the plain text requirement.
- Parsing PDF, Word, screenshots, scanned guideline images and other guideline formats can follow later.
- This format priority concerns guideline input. It does not remove the need to load images and annotations when auditing an image dataset.

## Sample data and rules

The screenshot says the shared resource contains sample data and a set of rules, followed by:

> Treat them as just examples

- Use shared examples to understand the workflow and develop checks.
- Do not treat one sample dataset or example rule set as the complete, fixed specification of the product.
- Penn-Fudan and the current RULES.md are local development examples. The screenshot does not establish that Penn-Fudan is the dataset in the mentors' shared resource.
- Keep the supplied guideline text and reviewer-confirmed rules separate from dataset-specific parsing and checking code.
- Example labels, thresholds and checks should not become universal rules for every dataset.

## Reviewer control

The earlier mentor reply requires the tool to identify possible errors and show both correct and erroneous data to the reviewer.

- The reviewer controls final decisions, including accepting or rejecting findings.
- The reviewer must be able to correct actual annotation errors.
- Confidence alone must not automatically accept or reject data at this stage.
- Review must include data where the tool found no issue, as well as flagged and uncertain cases.
- The tool must use reviewer corrections and feedback to improve future audits. Saving feedback is necessary, but storage alone does not fulfil this requirement; the improvement mechanism must be implemented and evaluated.

## Prototype and resources

The earlier mentor reply requires a working prototype on CPU using free/open-source models and tools. GPU implementation can follow after testing and performance evaluation.

- Determine the initial batch size from measured CPU performance and available resources.
- A particular Intel processor, Windows deployment and a 1,000-image batch are previous project assumptions, not confirmed mentor requirements.
- The existing project plan requires local, offline operation. This is an existing project constraint, separate from the quoted mentor reply.

## Deadline and working preferences

- The user stated a deadline of 31 October. Prioritize the plain text guideline workflow and a complete audit, review and correction path.
- The user handles Ollama model downloads. Do not download an Ollama model without a new instruction authorizing it.
- The user wants to write the application themselves. Assistance may explain steps, review code and debug when requested. Folder scaffolding is explicitly authorized; implementing application logic requires a new request.
- Keep separate `backend/` and `frontend/` folders. Current implementation work belongs in `backend/`; the frontend is reserved for later work.
- The user authorized installation of the other Python tools and the person detector. These are installed in the project's Python 3.12 virtual environment.
- The detector weights are cached under `.cache/torch/hub/checkpoints` and passed a CPU inference setup check on one Penn-Fudan image. This is not an accuracy or batch performance evaluation.
- The user selected `qwen3-vl:4b-instruct` for downloading. Completion of the download and model performance have not been confirmed.

## Relevant project files

- [README.md](README.md): current workflow and prototype constraints.
- [RULES.md](RULES.md): example Penn-Fudan rulebook, subject to reviewer confirmation.
- [requirements.txt](requirements.txt): installed direct Python dependency versions.
- [Data_Quality_Checker_Proposal_v3.md](Data_Quality_Checker_Proposal_v3.md): current proposal, revised to v3.1 on 13 October 2026 to incorporate these requirements and the 31 October delivery target.

"""Public transport models for liveness, capabilities and local readiness reads."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Health(BaseModel):
    """HTTP liveness only. It says nothing about Ollama, the detector or storage."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    status: Literal["ok"]
    api_version: Literal["v1"]


class CheckCapability(BaseModel):
    """An executable guideline check that rules may name, taken from the real registry."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(min_length=1)
    implementation: Literal["implemented"]


class EvidenceFeature(BaseModel):
    """Evidence stored with audited samples. It is not an executable guideline check."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    name: str = Field(min_length=1)
    implementation: Literal["implemented"]
    description: str = Field(min_length=1)


class Capabilities(BaseModel):
    """What this backend build can do. It never reflects which files are installed."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    dataset_formats: list[str]
    guideline_input_formats: list[str]
    coordinate_convention: Literal["format_specific"] = Field(
        description=(
            "Penn-Fudan uses one-based inclusive boxes. COCO and LabelMe use zero-based continuous "
            "coordinates. Detection evidence documents its own convention."
        ),
    )
    checks: list[CheckCapability] = Field(
        description=(
            "Executable guideline checks. Detection evidence is listed separately. A listed check can "
            "still report unavailable results for a sample when its inputs are missing; model-assisted "
            "checks, for example, need the configured local model (see system status)."
        ),
    )
    evidence_features: list[EvidenceFeature]
    execution_device: Literal["cpu"]


class OllamaStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    configuration: Literal["valid", "invalid"] = Field(
        description="Whether the local Ollama settings could be read."
    )
    reachable: bool = Field(description="Whether a short metadata request to the server succeeded.")
    model: str | None = Field(description="Configured language model name; null when settings are invalid.")
    model_available: bool | None = Field(
        description="Whether the configured model is installed; null when that is unknown."
    )

    @model_validator(mode="after")
    def unknown_states_report_no_availability(self):
        if self.configuration == "invalid" and (
            self.reachable or self.model is not None or self.model_available is not None
        ):
            raise ValueError("invalid Ollama settings leave every other field unknown")
        if not self.reachable and self.model_available is not None:
            raise ValueError("an unreachable server cannot report model availability")
        return self


class DetectorStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    integration_supported: bool = Field(
        description="Whether this environment has the packages the CPU detector needs."
    )
    configuration: Literal["disabled", "enabled", "invalid"] = Field(
        description="Detection settings for new audits; invalid means enabled but unusable."
    )
    checkpoint_file: Literal["not_configured", "present", "missing", "unreadable"] = Field(
        description=(
            "Whether a file exists at the configured location. A present file is not verified: "
            "it is not hashed, loaded or run."
        ),
    )
    validation: Literal["unknown", "validated", "failed"] = Field(
        description=(
            "Whether the checkpoint was actually loaded and checked. 'unknown' means no validation "
            "has been recorded; it never means the model works."
        ),
    )
    architecture: str = Field(min_length=1)


class PersonModelStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    model: str = Field(min_length=1, description="The segmentation model the person check uses.")
    checkpoint_file: Literal["present", "missing", "unreadable"] = Field(
        description=(
            "Whether the model's weights file is installed. A present file is not hashed, loaded or run "
            "by this read; the person check reports could not check for a sample when it cannot use it."
        ),
    )


class SystemStatus(BaseModel):
    """Read-only readiness. Reading it starts nothing and downloads nothing."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    ollama: OllamaStatus
    person_model: PersonModelStatus
    detector: DetectorStatus
    execution_device: Literal["cpu"]

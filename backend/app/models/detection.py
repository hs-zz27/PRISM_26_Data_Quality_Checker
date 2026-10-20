"""Person-detection evidence produced by the CPU detector.

These records describe what the detector saw. They carry no pass/fail meaning.
"""

from pydantic import BaseModel, ConfigDict, Field, model_validator

COORDINATE_CONVENTION = (
    "xyxy pixel-edge coordinates in the original image, as returned by torchvision: "
    "zero-based, origin at the top-left corner, x to the right, y downward. "
    "A box covering the whole image is (0, 0, width, height). "
    "These are not PRISM's one-based inclusive annotation coordinates."
)


class PersonDetection(BaseModel):
    """One detected person. Coordinates follow `COORDINATE_CONVENTION`."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    x_min: float = Field(ge=0, allow_inf_nan=False)
    y_min: float = Field(ge=0, allow_inf_nan=False)
    x_max: float = Field(allow_inf_nan=False)
    y_max: float = Field(allow_inf_nan=False)
    score: float = Field(ge=0, le=1, allow_inf_nan=False)

    @model_validator(mode="after")
    def require_nonempty_box(self):
        if self.x_max <= self.x_min or self.y_max <= self.y_min:
            raise ValueError("box must have positive width and height")
        return self


class DetectionResult(BaseModel):
    """All person detections at or above `min_score` for one image.

    An empty `detections` list means inference ran and found nobody. Failures
    are raised by the detector and never produce a result.
    """

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    image_width: int = Field(gt=0)
    image_height: int = Field(gt=0)
    min_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    model_name: str = Field(min_length=1)
    weights_file: str = Field(min_length=1)
    coordinate_convention: str = COORDINATE_CONVENTION
    detections: list[PersonDetection]

    @model_validator(mode="after")
    def require_boxes_inside_image(self):
        for box in self.detections:
            if box.x_max > self.image_width or box.y_max > self.image_height:
                raise ValueError("detection extends beyond the image")
        return self
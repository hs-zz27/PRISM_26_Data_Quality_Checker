"""Bridge a closed-over binary stream to the durable Phase 2 acceptance seam."""
from itertools import chain

from ..core.dataset_http import DatasetRequestError
from ..core.dataset_import import ImportAcceptanceError, ImportSettings
from .dataset_upload import CHUNK_BYTES, submit_archive


class DatasetUploadService:
    def __init__(self, repository, settings: ImportSettings):
        self.repository = repository
        self.settings = ImportSettings.model_validate(settings.model_dump())

    def submit(self, stream, *, name: str, format: str):
        """Blocking service; route offloads it, and parser context owns/closes stream."""
        try:
            stream.seek(0)
            first = stream.read(CHUNK_BYTES)
        except OSError as error:
            raise ImportAcceptanceError("The staged request stream could not be read.") from error
        if not first:
            raise DatasetRequestError("archive")
        # Every real byte is still measured again by the durable staging service.
        chunks = chain((first,), iter(lambda: stream.read(CHUNK_BYTES), b""))
        return submit_archive(chunks, name=name, format=format,
                              repository=self.repository, settings=self.settings)

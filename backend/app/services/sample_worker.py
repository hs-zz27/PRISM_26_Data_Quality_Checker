"""Spawn-safe, database-free execution of one sample collector."""

from __future__ import annotations

import pickle
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from ..core.config import AssetStorageSettings
from ..models.asset_manifest import SampleAssetManifest
from .asset_snapshot import materialize_sample


_MESSAGE_CHUNK_SIZE = 64 * 1024
_DATABASE_ENVIRONMENT_KEYS = (
    "DATABASE_URL", "POSTGRES_PASSWORD", "POSTGRES_USER", "POSTGRES_DB",
    "POSTGRES_HOST", "POSTGRES_PORT", "POSTGRES_CONNECT_TIMEOUT",
    "PGPASSWORD", "PGUSER", "PGDATABASE", "PGHOST", "PGPORT", "PGSERVICE",
    "PGSERVICEFILE",
)


def _clear_database_environment() -> None:
    """Remove inherited database settings from this child process only."""
    for name in _DATABASE_ENVIRONMENT_KEYS:
        os.environ.pop(name, None)


class SampleDataError(ValueError):
    """A collector reported an expected source-data problem."""


class TransientSampleError(RuntimeError):
    """A collector explicitly reported a retryable transient failure."""


class SampleComputationError(RuntimeError):
    """The isolated computation could not start, serialize or return safely."""


def _send_message(result_connection: Any, message: tuple[Any, ...]) -> None:
    """Send bounded frames so the parent can drain transport while we run."""
    try:
        payload = pickle.dumps(message, protocol=pickle.HIGHEST_PROTOCOL)
    except BaseException:  # noqa: BLE001 - report serialization failure safely
        payload = pickle.dumps((
            "fatal", "result_serialization", "sample result serialization failed",
        ), protocol=pickle.HIGHEST_PROTOCOL)
    try:
        for offset in range(0, len(payload), _MESSAGE_CHUNK_SIZE):
            result_connection.send_bytes(payload[offset:offset + _MESSAGE_CHUNK_SIZE])
        result_connection.send_bytes(b"")
    except BaseException:
        # A terminated parent may close the connection while the child reports.
        pass


def run_collector_child(
    result_connection: Any,
    collector: Callable[..., Any],
    dataset_root: str,
    sample_id: str,
    allowed_labels: list[str] | None,
    connectivity: int,
) -> None:
    """Run only the injected collector; this process never opens PostgreSQL."""
    _clear_database_environment()
    try:
        try:
            result = collector(dataset_root, sample_id, allowed_labels, connectivity)
        except (OSError, UnicodeError) as error:
            message = ("source_data", type(error).__name__, str(error))
        except SampleDataError as error:
            message = ("source_data", type(error).__name__, str(error))
        except TransientSampleError as error:
            message = ("transient", type(error).__name__, str(error))
        except BaseException as error:  # noqa: BLE001 - child reports all failures
            message = ("fatal", type(error).__name__, str(error))
        else:
            message = ("success", result)
        _send_message(result_connection, message)
    finally:
        result_connection.close()


def run_sample_child(
    result_connection: Any,
    collector: Callable[..., Any],
    evaluator: Callable[..., Any],
    rulebook: Any,
    dataset_root: str,
    sample_id: str,
    allowed_labels: list[str] | None,
    connectivity: int,
    audit_id: str,
    sample_revision: int,
    *,
    snapshot_manifest: SampleAssetManifest | None = None,
    asset_settings: AssetStorageSettings | None = None,
    feedback_runtime: Any = None,
    enclosure_runtime: Any = None,
    processing_deadline: datetime | None = None,
    reviewer_feedback: Any = None,
) -> None:
    """Collect and evaluate one sample without opening the workflow database."""
    _clear_database_environment()
    try:
        # Preparation belongs inside the supervised child. Storage failures are
        # fatal worker errors, distinct from errors while reading source data.
        if snapshot_manifest is not None:
            if asset_settings is None:
                raise SampleComputationError("Snapshot storage settings are required.")
            if snapshot_manifest.annotation_format == "penn_fudan":
                materialize_sample(
                    snapshot_manifest, asset_settings=asset_settings,
                    target_root=Path(dataset_root),
                )
        try:
            if snapshot_manifest is not None and snapshot_manifest.annotation_format != "penn_fudan":
                from .json_audit import audit_json_sample
                measurements = audit_json_sample(snapshot_manifest, asset_settings, allowed_labels)
            else:
                measurements = collector(dataset_root, sample_id, allowed_labels, connectivity)
        except (OSError, UnicodeError) as error:
            message = ("source_data", type(error).__name__, str(error))
        except SampleDataError as error:
            message = ("source_data", type(error).__name__, str(error))
        except TransientSampleError as error:
            message = ("transient", type(error).__name__, str(error))
        else:
            try:
                evaluation = evaluator(
                    rulebook,
                    measurements,
                    audit_id=audit_id,
                    sample_id=sample_id,
                    sample_revision=sample_revision,
                )
                if any(rule.check_name == "person_coverage" for rule in rulebook.rules):
                    from .person_coverage import augment_person_coverage
                    evaluation = augment_person_coverage(evaluation, rulebook, measurements, snapshot_manifest,
                        asset_settings, audit_id=audit_id, sample_id=sample_id, sample_revision=sample_revision)
                if feedback_runtime is not None:
                    if snapshot_manifest is None or asset_settings is None:
                        raise SampleComputationError("Model checks require immutable snapshot evidence.")
                    from .feedback import augment_evaluation
                    evaluation = augment_evaluation(evaluation,rulebook,snapshot_manifest,feedback_runtime,asset_settings,
                        audit_id=audit_id,sample_id=sample_id,sample_revision=sample_revision,
                        **({'reviewer_feedback': reviewer_feedback} if reviewer_feedback is not None else {}))
                if enclosure_runtime is not None:
                    if snapshot_manifest is None or asset_settings is None:
                        raise SampleComputationError("Model checks require immutable snapshot evidence.")
                    from .enclosure import augment_enclosure
                    evaluation = augment_enclosure(evaluation, rulebook, snapshot_manifest, enclosure_runtime, asset_settings,
                        processing_deadline=processing_deadline,
                        audit_id=audit_id, sample_id=sample_id, sample_revision=sample_revision,
                        **({'reviewer_feedback': reviewer_feedback} if reviewer_feedback is not None else {}),
                        **({'feedback_policy': feedback_runtime.policy} if feedback_runtime is not None
                            and feedback_runtime.execution.enclosure is not None else {}))
                if reviewer_feedback is not None:
                    from .review_correction import attach_review_context
                    measurements = attach_review_context(measurements, reviewer_feedback, snapshot_manifest,
                        rulebook, asset_settings, feedback_runtime, enclosure_runtime, evaluation=evaluation)
                message = (
                    "success", measurements, evaluation.findings,
                    evaluation.internal_check_failures,
                )
            except BaseException as error:  # noqa: BLE001 - supervisor classifies fatal computation
                message = ("fatal", type(error).__name__, str(error))
    except BaseException as error:  # noqa: BLE001 - child must report all failures
        message = ("fatal", type(error).__name__, str(error))
    _send_message(result_connection, message)
    result_connection.close()

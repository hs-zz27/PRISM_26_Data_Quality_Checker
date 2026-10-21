"""Synthetic export evidence only. Nothing is created until a test calls it."""
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from backend.app.core.export import ExportSettings
from backend.app.models.asset_manifest import AssetManifestEntry, SampleAssetManifest
from backend.app.models.export import (
    CapturedAvailability, ExportManifest, ExportManifestEntry, ExportPolicy,
    FrozenExport, FrozenSample, exclusion, ExportSelection,
)
from backend.app.models.review import SampleReviewDecision

NOW = datetime(2026, 10, 21, tzinfo=UTC)
EXPORT_ID = 'export_' + 'a' * 32


def synthetic(tmp_path, decisions=('accepted',), *, include_images=True, selection='accepted_only',
              missing=None, policy=None, ids=None):
    root = tmp_path / 'exports'
    assets = tmp_path / 'assets'
    assets.mkdir(mode=0o700, exist_ok=True)
    samples, expected = [], {}
    for ordinal, decision in enumerate(decisions):
        sample_id = f'sample_{ordinal}' if ids is None else ids[ordinal]
        roles = {}
        # Includes malformed annotation text, CRLF, NUL and high raw mask IDs.
        # Export is byte preservation, not a new source-content validator.
        for role, content in [('annotation_text', b'broken label\r\n\x00 (1, 1)\xff'),
                              ('raw_mask', b'\x89PNG\r\n\x1a\n\x00\x01\xff\xffraw-mask'),
                              ('original_image', b'\x89PNG\r\n\x1a\nraw-image')]:
            availability = (missing or {}).get((ordinal, role), 'available')
            if availability != 'available':
                roles[role] = AssetManifestEntry(availability=availability)
                continue
            key = f'synthetic/{ordinal}/{role}'
            path = assets / key
            path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            path.write_bytes(content)
            roles[role] = AssetManifestEntry(availability='available', storage_key=key,
                                              content_type='text/plain' if role == 'annotation_text' else 'image/png',
                                              sha256=sha256(content).hexdigest(), size_bytes=len(content))
            expected[(sample_id, role)] = content
        manifest = SampleAssetManifest(dataset_id='dataset_demo', sample_id=sample_id,
                                       sample_revision=2, **roles)
        availability = CapturedAvailability(**{k: v.availability for k, v in roles.items()})
        reason = exclusion(SampleReviewDecision(decision), ExportSelection(selection), include_images, availability)
        entry = ExportManifestEntry(sample_id=sample_id, sample_revision=2, ordinal=ordinal,
                                    processing_state='failed' if ordinal == 0 else 'completed',
                                    included=reason is None, review_decision=decision, review_revision=7,
                                    reviewer_id='reviewer_demo', reviewed_at=NOW,
                                    availability=availability, reason=reason)
        samples.append(FrozenSample(entry=entry, manifest=manifest))
    document = ExportManifest(export_id=EXPORT_ID, audit_id='audit_demo', dataset_id='dataset_demo',
                              source_mode='snapshot', rulebook_id='rulebook_demo', rulebook_version=3,
                              selection=selection, include_images=include_images, selected_at=NOW,
                              entries=[s.entry for s in samples])
    policy = ExportPolicy() if policy is None else policy
    frozen = FrozenExport(manifest=document, samples=samples, policy=policy)
    settings = ExportSettings(root=root, asset_root=assets, import_root=tmp_path / 'imports', policy=policy)
    return frozen, settings, expected

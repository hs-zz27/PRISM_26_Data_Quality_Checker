"""Correction transactions: dataset -> head -> staged mask; never file I/O."""
from contextlib import contextmanager
from datetime import timedelta
import json
from typing import Any
from uuid import uuid4

from pydantic import BaseModel
from psycopg.types.json import Jsonb

from ..core.correction import CorrectionError, CorrectionStorageError, CorrectionCommitUnknown
from ..models.correction import AnnotationRevision, RevisionPage, StagedMask, MaskUpload, PreparedCorrection
from ..models.asset_manifest import AssetManifestEntry
from ..models.dataset_import import SamplePublication
from .database import database_connection, CommitOutcomeUnknownError, DatabaseError
from .dataset_repository import DatasetRepository, DatasetStorageError, DatasetPublicationConflictError, _pagination, _identifier, _integer


def _validation_input(value: Any) -> Any:
    """Detach nested models without letting serializers normalize invalid fields."""
    if isinstance(value, BaseModel):
        return {name: _validation_input(item) for name, item in value}
    if isinstance(value, dict):
        return {name: _validation_input(item) for name, item in value.items()}
    if isinstance(value, list):
        return [_validation_input(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_validation_input(item) for item in value)
    return value


def checked[Model: BaseModel](model: type[Model], value: object) -> Model:
    """Revalidate live Python values before any JSON serialization or storage."""
    if isinstance(value, model):
        return model.model_validate(_validation_input(value))
    # Stored JSON documents need JSON-mode tuple/date decoding. They have not
    # passed through a model serializer, so invalid scalar types remain intact.
    return model.model_validate_json(json.dumps(value, allow_nan=False))


class CorrectionRepository(DatasetRepository):
    @contextmanager
    def _connection(self, *, write=False):
        try:
            with database_connection(self.settings, readonly=not write) as connection:
                yield connection
        except CommitOutcomeUnknownError as error:
            raise CorrectionCommitUnknown() from error
        except (DatabaseError, DatasetStorageError, DatasetPublicationConflictError) as error:
            raise CorrectionStorageError() from error

    def base(self, dataset_id, sample_id, expected):
        self._read_key(dataset_id, sample_id, expected)
        with self._connection() as connection:
            base = self._get_publication(connection, dataset_id, sample_id, None)
            if base.sample.revision != expected:
                raise CorrectionError('STALE_SAMPLE_REVISION')
            if expected == 2147483647:
                raise CorrectionError('VALIDATION_ERROR')
            return base

    def _lock_base(self, connection, dataset_id, sample_id, expected):
        dataset = connection.execute('SELECT * FROM datasets WHERE dataset_id=%s FOR UPDATE', (dataset_id,)).fetchone()
        self._dataset(connection, dataset_id)
        if dataset['status'] != 'ready':
            raise CorrectionError('SOURCE_NOT_CORRECTABLE')
        head = connection.execute('SELECT * FROM dataset_sample_heads WHERE dataset_id=%s AND sample_id=%s FOR UPDATE', (dataset_id,sample_id)).fetchone()
        if head is None:
            self._get_publication(connection,dataset_id,sample_id,None)
            raise CorrectionStorageError()
        if head['sample_revision'] != expected:
            raise CorrectionError('STALE_SAMPLE_REVISION')
        return dataset, self._get_publication(connection,dataset_id,sample_id,expected)

    @staticmethod
    def source_audit(connection, source_id, base):
        if source_id is None:
            return
        row = connection.execute('''SELECT a.dataset_id,a.source_mode,s.manifest_dataset_id,s.sample_revision
            FROM audit_runs a JOIN audit_samples s USING(audit_id)
            WHERE a.audit_id=%s AND s.sample_id=%s''', (source_id,base.sample.sample_id)).fetchone()
        if row is None or (row['dataset_id'],row['manifest_dataset_id'],row['sample_revision'],row['source_mode']) != (
                base.sample.dataset_id,base.sample.dataset_id,base.sample.revision,'snapshot'):
            raise CorrectionError('SOURCE_AUDIT_MISMATCH')

    def stage(self, base, asset, image_size, mask_ids, *, ttl_seconds):
        base = checked(SamplePublication,base)
        asset = checked(AssetManifestEntry,asset)
        _integer(ttl_seconds,'ttl_seconds',60,86400)
        with self._connection(write=True) as connection:
            _, saved = self._lock_base(connection,base.sample.dataset_id,base.sample.sample_id,base.sample.revision)
            if saved != base:
                raise CorrectionStorageError()
            now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            public = MaskUpload(mask_upload_id='mask_'+uuid4().hex,dataset_id=base.sample.dataset_id,
                                sample_id=base.sample.sample_id,base_revision=base.sample.revision,
                                image_size=image_size,mask_ids=mask_ids,expires_at=now+timedelta(seconds=ttl_seconds))
            staged = StagedMask(public=public,asset=asset,created_at=now)
            connection.execute('''INSERT INTO correction_mask_uploads
                (mask_upload_id,dataset_id,sample_id,base_revision,document,created_at,expires_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''', (public.mask_upload_id,public.dataset_id,public.sample_id,
                public.base_revision,Jsonb(staged.model_dump(mode='json')),now,public.expires_at))
        return public

    @staticmethod
    def _decode_mask(row):
        try:
            staged = checked(StagedMask,row['document'])
            public = staged.public
            if (public.mask_upload_id,public.dataset_id,public.sample_id,public.base_revision,staged.created_at,public.expires_at) != (
                    row['mask_upload_id'],row['dataset_id'],row['sample_id'],row['base_revision'],row['created_at'],row['expires_at']):
                raise ValueError('Stored staging identity mismatch.')
            if staged.consumed_revision is not None:
                raise ValueError('Original staging document must remain unconsumed.')
            return StagedMask.model_validate({**staged.model_dump(),'consumed_revision':row['consumed_revision']})
        except (KeyError,TypeError,ValueError) as error:
            raise CorrectionStorageError() from error

    def _mask(self, connection, mask_id, base, *, lock=False):
        row = connection.execute('SELECT * FROM correction_mask_uploads WHERE mask_upload_id=%s'+(' FOR UPDATE' if lock else ''),(mask_id,)).fetchone()
        if row is None:
            raise CorrectionError('MASK_UPLOAD_UNAVAILABLE')
        staged = self._decode_mask(row)
        public = staged.public
        now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
        if ((public.dataset_id,public.sample_id,public.base_revision) != (base.sample.dataset_id,base.sample.sample_id,base.sample.revision)
                or staged.consumed_revision is not None or public.expires_at <= now):
            raise CorrectionError('MASK_UPLOAD_UNAVAILABLE')
        return staged

    def mask(self, mask_id, base):
        _identifier(mask_id,'mask_upload_id')
        base = checked(SamplePublication,base)
        with self._connection() as connection:
            return self._mask(connection,mask_id,base)

    def publish(self, prepared):
        prepared = checked(PreparedCorrection,prepared)
        request, base, revised = prepared.request,prepared.base,prepared.revised
        key = (base.sample.dataset_id,base.sample.sample_id)
        with self._connection(write=True) as connection:
            dataset,saved = self._lock_base(connection,*key,request.expected_revision)
            if saved != base:
                raise CorrectionStorageError()
            self.source_audit(connection,request.source_audit_id,base)
            staged = self._mask(connection,request.mask_upload_id,base,lock=True) if request.mask_upload_id is not None else None
            if revised.manifest.raw_mask != (base.manifest.raw_mask if staged is None else staged.asset):
                raise CorrectionStorageError()
            if staged is not None and staged.public.image_size != revised.sample.image_size:
                raise CorrectionStorageError()
            now = connection.execute('SELECT clock_timestamp() AS now').fetchone()['now']
            self._insert_manifest(connection,revised.manifest)
            connection.execute('''INSERT INTO dataset_sample_revisions
                (dataset_id,sample_id,sample_revision,sample_document,manifest_document,annotation_object_count,created_at)
                VALUES(%s,%s,%s,%s,%s,%s,%s)''',(*key,revised.sample.revision,revised.sample.model_dump_json(),
                Jsonb(revised.manifest.model_dump(mode='json')),revised.annotation_object_count,now))
            connection.execute('''INSERT INTO annotation_revision_provenance
                (dataset_id,sample_id,sample_revision,parent_revision,reviewer_id,reason,source_audit_id,mask_changed,mask_upload_id)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)''',(*key,revised.sample.revision,base.sample.revision,request.reviewer_id,
                request.reason,request.source_audit_id,staged is not None,request.mask_upload_id))
            if dataset['object_count'] is not None:
                if base.annotation_object_count is None or dataset['object_count'] < base.annotation_object_count:
                    raise CorrectionStorageError()
                total = dataset['object_count'] - base.annotation_object_count + revised.annotation_object_count
                if not 0 <= total <= 9223372036854775807:
                    raise CorrectionStorageError()
                connection.execute('UPDATE datasets SET object_count=%s WHERE dataset_id=%s',(total,key[0]))
            changed = connection.execute('''UPDATE dataset_sample_heads SET sample_revision=%s
                WHERE dataset_id=%s AND sample_id=%s AND sample_revision=%s RETURNING sample_revision''',
                (revised.sample.revision,*key,base.sample.revision)).fetchone()
            if changed is None:
                raise CorrectionError('STALE_SAMPLE_REVISION')
            if staged is not None:
                # Check server time AGAIN after all index/constraint waits. Roll back
                # the whole publication if expiry wins before final consumption.
                consumed = connection.execute('''UPDATE correction_mask_uploads SET consumed_revision=%s
                    WHERE mask_upload_id=%s AND consumed_revision IS NULL AND expires_at>clock_timestamp()
                    RETURNING mask_upload_id''',(revised.sample.revision,request.mask_upload_id)).fetchone()
                if consumed is None:
                    raise CorrectionError('MASK_UPLOAD_UNAVAILABLE')
            result = self._history_item(connection,*key,revised.sample.revision)
        return result

    def _history_item(self, connection, dataset_id, sample_id, revision):
        saved = self._get_publication(connection,dataset_id,sample_id,revision)
        row = connection.execute('''SELECT r.created_at,p.parent_revision,p.reviewer_id,p.reason,
            p.source_audit_id,p.mask_changed FROM dataset_sample_revisions r
            LEFT JOIN annotation_revision_provenance p USING(dataset_id,sample_id,sample_revision)
            WHERE r.dataset_id=%s AND r.sample_id=%s AND r.sample_revision=%s''',(dataset_id,sample_id,revision)).fetchone()
        try:
            return AnnotationRevision(dataset_id=dataset_id,sample_id=sample_id,revision=revision,
                objects=saved.sample.objects,created_at=row['created_at'],parent_revision=row['parent_revision'],
                reviewer_id=row['reviewer_id'],reason=row['reason'],source_audit_id=row['source_audit_id'],
                mask_changed=(False if revision == 1 else row['mask_changed']))
        except (KeyError,TypeError,ValueError) as error:
            raise CorrectionStorageError() from error

    def history(self,dataset_id,sample_id,*,limit=20,offset=0):
        self._read_key(dataset_id,sample_id,None)
        _pagination(limit,offset)
        with self._connection() as connection:
            self._get_publication(connection,dataset_id,sample_id,None)
            total = connection.execute('SELECT count(*) AS n FROM dataset_sample_revisions WHERE dataset_id=%s AND sample_id=%s',(dataset_id,sample_id)).fetchone()['n']
            rows = connection.execute('''SELECT sample_revision FROM dataset_sample_revisions WHERE dataset_id=%s AND sample_id=%s
                ORDER BY sample_revision DESC LIMIT %s OFFSET %s''',(dataset_id,sample_id,limit,offset)).fetchall()
            items = [self._history_item(connection,dataset_id,sample_id,r['sample_revision']) for r in rows]
            return RevisionPage(items=items,limit=limit,offset=offset,total=total,
                next_offset=offset+len(items) if offset+len(items)<total else None)

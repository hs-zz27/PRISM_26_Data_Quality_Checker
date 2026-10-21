"""Linked rechecks use AUDIT jobs and the existing complete supervised lifecycle."""
from ..core.correction import CorrectionError, CorrectionStorageError
from ..models.audit import BatchAuditRequest
from ..models.audit_submission import AuditStarted
from ..models.correction import RecheckRequest
from .correction_repository import CorrectionRepository, checked
from .dataset_repository import _identifier
from .workflow_repository import AuditRepository, AuditNotFoundError, _decode_audit
from .repository import RulebookRepository
from ..services.batch_audit import _snapshot_confirmed_rulebook


class RecheckRepository(CorrectionRepository):
    def start(self,parent_audit_id,request):
        _identifier(parent_audit_id,'parent_audit_id')
        request=checked(RecheckRequest,request)
        # Read exact parent and book before taking catalogue write locks. Historical
        # book versions are immutable; re-read and compare parent request in write TX.
        with self._connection() as connection:
            parent=self._parent(connection,parent_audit_id)
        book=RulebookRepository(settings=self.settings).get_rulebook(parent['rulebook_id'],version=parent['rulebook_version'])
        from ..checks.registry import RuleParameterError
        from ..services.audit_submission import AuditRuleParametersError
        try:
            _snapshot_confirmed_rulebook(book)
        except RuleParameterError as error:
            raise AuditRuleParametersError("The recorded rule parameters are invalid.") from error
        if (book.rulebook_id,book.version)!=(parent['rulebook_id'],parent['rulebook_version']):
            raise CorrectionStorageError()
        with self._connection(write=True) as connection:
            current=self._parent(connection,parent_audit_id)
            if current['request']!=parent['request'] or (current['dataset_id'],current['rulebook_id'],current['rulebook_version'])!=(parent['dataset_id'],parent['rulebook_id'],parent['rulebook_version']):
                raise CorrectionStorageError()
            original=checked(BatchAuditRequest,current['request'])
            # Dataset first, then heads in stable order. All correction writers use
            # this same prefix; head changes cannot pass validation before commit.
            dataset=connection.execute('SELECT * FROM datasets WHERE dataset_id=%s FOR UPDATE',(current['dataset_id'],)).fetchone()
            if dataset is None or dataset['status']!='ready':
                raise CorrectionError('AUDIT_NOT_RECHECKABLE')
            # Decode the locked catalogue row through the established validator;
            # corrupt metadata is storage failure, not a successful child enqueue.
            self._dataset(connection,current['dataset_id'])
            parent_rows=connection.execute('''SELECT * FROM audit_samples WHERE audit_id=%s AND sample_id=ANY(%s)''',
                (parent_audit_id,request.sample_ids)).fetchall()
            by_id={r['sample_id']:r for r in parent_rows}
            if len(parent_rows)!=len(request.sample_ids):
                raise CorrectionError('VALIDATION_ERROR')
            for sample_id in request.sample_ids:
                row=by_id[sample_id]
                if (row['manifest_dataset_id']!=current['dataset_id'] or original.sample_revisions.get(sample_id)!=row['sample_revision']):
                    raise CorrectionError('AUDIT_NOT_RECHECKABLE')
                # Validate parent immutable catalogue/registered manifest equality,
                # not merely that a sample name exists in the current catalogue.
                self._get_publication(connection,current['dataset_id'],sample_id,row['sample_revision'])
            heads=connection.execute('''SELECT * FROM dataset_sample_heads WHERE dataset_id=%s AND sample_id=ANY(%s)
                ORDER BY sample_id COLLATE "C" FOR UPDATE''',(current['dataset_id'],request.sample_ids)).fetchall()
            if len(heads)!=len(request.sample_ids):
                raise CorrectionStorageError()
            if any(r['sample_revision']!=request.sample_revisions[r['sample_id']] for r in heads):
                raise CorrectionError('STALE_SAMPLE_REVISION')
            pins={sample_id:self._get_publication(connection,current['dataset_id'],sample_id,request.sample_revisions[sample_id]).manifest for sample_id in request.sample_ids}
            batch=BatchAuditRequest.model_validate({**original.model_dump(),'dataset_root':'','sample_ids':request.sample_ids,
                'sample_revisions':request.sample_revisions,'source_mode':'snapshot'})
            # Private shared insertion seam; no second connection or generic queue.
            audit,job=AuditRepository(settings=self.settings)._start_audit_locked(connection,batch,sample_manifests=pins)
            connection.execute('''INSERT INTO audit_recheck_lineage(child_audit_id,parent_audit_id,reviewer_id,created_at)
                VALUES(%s,%s,%s,%s)''',(audit.audit_id,parent_audit_id,request.reviewer_id,audit.created_at))
            result=AuditStarted(audit=audit,job=job,parent_audit_id=parent_audit_id,feedback_policy_id=original.feedback_policy_id)
        return result

    @staticmethod
    def _parent(connection,audit_id):
        row=connection.execute('SELECT * FROM audit_runs WHERE audit_id=%s',(audit_id,)).fetchone()
        if row is None:
            raise AuditNotFoundError(audit_id)
        audit=_decode_audit(row)
        if audit.status.value not in {'completed','failed','cancelled'}:
            raise CorrectionError('AUDIT_NOT_READY')
        try:
            original=checked(BatchAuditRequest,row['request'])
        except (TypeError,ValueError) as error:
            raise CorrectionStorageError() from error
        if (audit.source_mode.value!='snapshot' or original.source_mode.value!='snapshot'
                or (original.feedback_policy_id is not None and original.feedback_runtime is None)):
            raise CorrectionError('AUDIT_NOT_RECHECKABLE')
        if ((audit.dataset_id,audit.rulebook_id,audit.rulebook_version)!=(original.dataset_id,original.rulebook_id,original.rulebook_version)
                or audit.sample_count!=len(original.sample_ids) or set(original.sample_revisions)!=set(original.sample_ids)):
            raise CorrectionStorageError()
        return row

"""Tiny real PNG catalogue fixtures; all work occurs only when tests invoke it."""
from hashlib import sha256
from io import BytesIO
from uuid import uuid4

import numpy as np
from PIL import Image

from backend.app.core.correction import CorrectionSettings
from backend.app.models.asset_manifest import AssetManifestEntry, SampleAssetManifest
from backend.app.models.dataset import Sample
from backend.app.models.dataset_import import SamplePublication
from backend.app.models.correction import CorrectionObject
from backend.app.services.corrections import CorrectionService, annotation_bytes
from backend.app.storage.correction_repository import CorrectionRepository
from backend.app.storage.dataset_repository import DatasetRepository
from backend.tests.dataset_fixtures import seed_import, write_sql


def png(array=None, *, mode=None):
    image=Image.new('RGB',(4,3)) if array is None else Image.fromarray(array)
    if mode is not None:
        image=image.convert(mode)
    output=BytesIO();image.save(output,format='PNG');return output.getvalue()


def fixture(settings,tmp_path,*,unknown_count=False,broken_annotation=False):
    root=tmp_path/'assets';root.mkdir(mode=0o700,exist_ok=True)
    mask=png(np.ones((3,4),dtype=np.uint16))
    obj=CorrectionObject(object_id=1,label='person',bbox=(1,1,4,3))
    annotation=b'broken annotation' if broken_annotation else annotation_bytes([obj],(4,3),3,1024)
    assets={}
    for role,data in [('original_image',png()),('raw_mask',mask),('annotation_text',annotation)]:
        key='synthetic/'+uuid4().hex;path=root/key;path.parent.mkdir(mode=0o700,exist_ok=True);path.write_bytes(data)
        assets[role]=AssetManifestEntry(availability='available',storage_key=key,content_type='text/plain' if role=='annotation_text' else 'image/png',sha256=sha256(data).hexdigest(),size_bytes=len(data))
    manifest=SampleAssetManifest(dataset_id='dataset_a',sample_id='sample_a',sample_revision=1,**assets)
    from backend.app.models.dataset import source_issue
    sample=Sample(dataset_id='dataset_a',sample_id='sample_a',revision=1,image_size=(4,3),
        integrity_status='invalid' if broken_annotation else 'valid',objects=[] if broken_annotation else [obj.model_dump()],
        issues=[source_issue('invalid_annotation',field='annotation')] if broken_annotation else [])
    publication=SamplePublication(manifest=manifest,sample=sample,ordinal=0,annotation_object_count=None if broken_annotation else 1)
    _,_,claim=seed_import(settings)
    DatasetRepository(settings=settings)._publish_import_sample(claim,publication)
    write_sql(settings,"UPDATE datasets SET status='ready',sample_count=1,object_count=%s WHERE dataset_id='dataset_a'",(None if unknown_count or broken_annotation else 1,))
    repository=CorrectionRepository(settings=settings)
    config=CorrectionSettings(asset_root=root)
    return repository,CorrectionService(repository,config),publication

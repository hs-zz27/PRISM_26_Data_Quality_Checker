"""Real corrected evidence; no source re-encoding, DB locks during decoding or acceptance inference."""
from io import BytesIO
import struct

import numpy as np
from PIL import Image, UnidentifiedImageError

from ..core.correction import CorrectionError, CorrectionSettings, CorrectionStorageError
from ..datasets.penn_fudan import parse_annotations, parse_annotation_metadata
from ..models.asset_manifest import SampleAssetManifest
from ..models.correction import CorrectionRequest, PreparedCorrection
from ..models.dataset import Sample
from ..models.dataset_import import SamplePublication
from ..storage.correction_repository import checked
from .correction_files import read_asset, write_asset


def decode_png(data, policy, *, mask=False):
    try:
        if len(data)<33 or data[:8]!=b'\x89PNG\r\n\x1a\n' or data[12:16]!=b'IHDR':
            raise ValueError('Not PNG.')
        if mask:
            depth,color=struct.unpack('BB',data[24:26])
            if not ((color==0 and depth in (8,16)) or (color==3 and depth in (1,2,4,8))):
                raise ValueError('Unsupported integer-ID PNG representation.')
        # verify() and load() use independent handles to the same immutable bytes.
        with Image.open(BytesIO(data)) as image:
            if image.format!='PNG' or getattr(image,'n_frames',1)!=1 or image.width*image.height>policy.max_pixels:
                raise ValueError('Image bounds or representation exceed policy.')
            if image.width<1 or image.height<1:
                raise ValueError('Empty image.')
            image.verify()
        with Image.open(BytesIO(data)) as image:
            image.load()
            size=image.size
            if not mask:
                return size,len(image.getbands())
            if image.mode not in ('L','P','I','I;16','I;16B','I;16L'):
                raise ValueError('Mask is not raw integer IDs.')
            array=np.array(image)
            if array.ndim!=2 or array.dtype.kind not in ('i','u') or int(array.min())<0 or int(array.max())>65535:
                raise ValueError('Mask IDs outside supported range.')
            return size,[int(v) for v in np.unique(array)]
    except (OSError,ValueError,UnidentifiedImageError,Image.DecompressionBombError,OverflowError,SyntaxError,struct.error) as error:
        raise CorrectionError('SOURCE_NOT_CORRECTABLE') from error


def annotation_bytes(objects, size, channels, max_bytes):
    lines=[f'Image size (X x Y x C) : {size[0]} x {size[1]} x {channels}',
           f'Objects with ground truth : {len(objects)} {{'+ ' '.join(str(o.object_id) for o in objects) +'}',
           'Top left pixel co-ordinates : (1, 1)']
    for obj in objects:
        x0,y0,x1,y1=obj.bbox
        if x1>size[0] or y1>size[1]:
            raise CorrectionError('VALIDATION_ERROR')
        lines.append(f'Bounding box for object {obj.object_id} "{obj.label}" (Xmin, Ymin) - (Xmax, Ymax) : ({x0}, {y0}) - ({x1}, {y1})')
    text='\n'.join(lines)+'\n'
    data=text.encode('utf-8')
    if len(data)>max_bytes:
        raise CorrectionError('UPLOAD_TOO_LARGE')
    expected=[obj.model_dump() for obj in objects]
    metadata=parse_annotation_metadata(text)
    if (parse_annotations(text)!=expected or metadata['image_size']!=(*size,channels)
            or metadata['object_count']!=len(objects) or metadata['coordinate_origin']!=(1,1) or metadata['errors']):
        raise RuntimeError('Canonical annotation serialization is inconsistent.')
    return data


class CorrectionService:
    def __init__(self,repository,settings):
        self.repository=repository
        self.settings=self._validated_settings(settings)

    @staticmethod
    def _validated_settings(settings):
        try:
            return CorrectionSettings.model_validate(settings.model_dump())
        except (ValueError,TypeError,AttributeError) as error:
            raise CorrectionStorageError() from error

    def _settings_snapshot(self):
        # A fresh independent nested record per operation; no caller mutation can
        # weaken resource checks midway through decoding or publication.
        return self._validated_settings(self.settings)

    def upload(self,dataset_id,sample_id,expected_revision,handle):
        settings=self._settings_snapshot()
        base=self.repository.base(dataset_id,sample_id,expected_revision)
        if base.manifest.annotation_format != 'penn_fudan':
            raise CorrectionError('JSON_SOURCE_NOT_CORRECTABLE')
        image_size,_=decode_png(read_asset(settings,base.manifest.original_image),settings.policy)
        chunks=[]; total=0; limit=settings.policy.max_mask_bytes
        while True:
            chunk=handle.read(min(65536,limit-total+1))
            if not chunk:
                break
            total+=len(chunk)
            if total>limit:
                raise CorrectionError('UPLOAD_TOO_LARGE')
            chunks.append(chunk)
        data=b''.join(chunks)
        size,ids=decode_png(data,settings.policy,mask=True)
        if size!=image_size:
            raise CorrectionError('VALIDATION_ERROR')
        asset=write_asset(settings,data,role='mask')
        # Head CAS again after decoding/writing, no locks held during that work.
        return self.repository.stage(base,asset,size,ids,ttl_seconds=settings.policy.upload_ttl_seconds)

    def correct(self,dataset_id,sample_id,request):
        settings=self._settings_snapshot()
        request=checked(CorrectionRequest,request)
        base=self.repository.base(dataset_id,sample_id,request.expected_revision)
        if base.manifest.annotation_format != 'penn_fudan':
            from .json_corrections import prepare_json_correction
            return self.repository.publish(prepare_json_correction(base, request, settings))
        if any(hasattr(obj, 'coordinate_system') for obj in request.objects):
            raise CorrectionError('VALIDATION_ERROR')
        size,channels=decode_png(read_asset(settings,base.manifest.original_image),settings.policy)
        staged=self.repository.mask(request.mask_upload_id,base) if request.mask_upload_id is not None else None
        mask=base.manifest.raw_mask if staged is None else staged.asset
        mask_data=read_asset(settings,mask)
        mask_size,ids=decode_png(mask_data,settings.policy,mask=True)
        if mask_size!=size or set(ids)-{0}!={obj.object_id for obj in request.objects}:
            raise CorrectionError('VALIDATION_ERROR')
        if staged is not None and (staged.public.image_size!=size or staged.public.mask_ids!=ids):
            raise CorrectionStorageError()
        annotation=write_asset(settings,annotation_bytes(request.objects,size,channels,settings.policy.max_annotation_bytes),role='annotation')
        revision=base.sample.revision+1
        manifest=SampleAssetManifest(dataset_id=dataset_id,sample_id=sample_id,sample_revision=revision,
            original_image=base.manifest.original_image,raw_mask=mask,annotation_text=annotation)
        sample=Sample(dataset_id=dataset_id,sample_id=sample_id,revision=revision,image_size=size,
            integrity_status='valid',objects=[obj.model_dump() for obj in request.objects],issues=[])
        revised=SamplePublication(manifest=manifest,sample=sample,ordinal=base.ordinal,annotation_object_count=len(request.objects))
        return self.repository.publish(PreparedCorrection(request=request,base=base,revised=revised))

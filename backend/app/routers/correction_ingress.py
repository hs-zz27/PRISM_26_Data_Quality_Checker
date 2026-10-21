"""Correction/recheck-only actual request byte guard; no global upload policy change."""
import re

from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect
from starlette.exceptions import HTTPException

from ..core.correction import CorrectionError, CorrectionSettings, CorrectionStorageError, load_correction_settings
from .errors import _response

SCOPE_KEY='prism.correction_settings'
PATH=re.compile(r'^/api/v1/(?:datasets/[^/]+/samples/[^/]+/(?:mask-uploads|revisions)|audits/[^/]+/rechecks)/?$')


class CorrectionBodyLimit(HTTPException):
    """HTTPException survives FastAPI's JSON-body parsing exception boundary."""
    def __init__(self):
        super().__init__(status_code=413)


class CorrectionIngress:
    def __init__(self,app,*,settings_factory=None):
        self.app=app
        self.factory=load_correction_settings if settings_factory is None else settings_factory

    async def __call__(self,scope,receive,send):
        path=scope.get('path','');root=scope.get('root_path','').rstrip('/')
        if root and (path==root or path.startswith(root+'/')):
            path=path[len(root):]
        if scope['type']!='http' or scope['method']!='POST' or not PATH.fullmatch(path):
            await self.app(scope,receive,send)
            return
        started=False
        async def guarded_send(message):
            nonlocal started
            if message['type']=='http.response.start':
                started=True
                if message['status']>=400 and scope.get('http_version') in ('1.0','1.1'):
                    message={**message,'headers':[(k,v) for k,v in message.get('headers',[]) if k.lower()!=b'connection']+[(b'connection',b'close')]}
            await send(message)
        try:
            try:
                settings=await run_in_threadpool(self.factory)
                settings=CorrectionSettings.model_validate(settings.model_dump())
            except (ValueError,TypeError,AttributeError) as error:
                raise CorrectionStorageError() from error
            scope[SCOPE_KEY]=settings
            lengths=[v for k,v in scope.get('headers',[]) if k.lower()==b'content-length']
            encodings=[v for k,v in scope.get('headers',[]) if k.lower()==b'content-encoding']
            if len(encodings)>1 or (encodings and encodings[0].strip().lower()!=b'identity'):
                raise CorrectionError('UNSUPPORTED_MEDIA_TYPE')
            if len(lengths)>1 or (lengths and not re.fullmatch(rb'[0-9]{1,20}',lengths[0])):
                raise CorrectionError('VALIDATION_ERROR')
            if lengths and int(lengths[0])>settings.body_limit:
                raise CorrectionError('UPLOAD_TOO_LARGE')
            total=0
            async def bounded_receive():
                nonlocal total
                message=await receive()
                if message['type']=='http.request':
                    total+=len(message.get('body',b''))
                    if total>settings.body_limit:
                        raise CorrectionBodyLimit()
                return message
            await self.app(scope,bounded_receive,guarded_send)
        except CorrectionError as error:
            if started:
                raise
            await _response(error.status,error.code,str(error))(scope,receive,guarded_send)
        except ClientDisconnect:
            return

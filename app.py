import hashlib
import hmac
import json
import logging
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import parse_qs

from .config import Settings
from .gemini import GeminiClient
from .kb import (
    SECRET_PATTERNS,
    FastEmbedder,
    PostgresVectorStore,
    SQLiteKBStore,
    refresh_from_files,
    refresh_workflow_library,
)
from .meta import MetaClient, extract_message_events, verify_signature
from .service import MEDICAL_REQUEST, PERSONAL_DATA, PRIVATE_REFERENCE, ChatService

logger = logging.getLogger(__name__)


class RateLimiter:
    def __init__(self, max_requests, window_seconds=60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.requests = defaultdict(deque)

    def allow(self, key, now=None):
        now = time.monotonic() if now is None else now
        recent = self.requests[key]
        while recent and recent[0] <= now - self.window_seconds:
            recent.popleft()
        if len(recent) >= self.max_requests:
            return False
        recent.append(now)
        return True


class SupportBotApplication:
    def __init__(self, settings=None, store=None, generator=None, meta_client=None):
        self.settings = settings or Settings.from_env()
        if store is not None:
            self.store = store
        elif getattr(self.settings, 'database_url', ''):
            embedder = FastEmbedder(getattr(
                self.settings, 'embedding_model', 'intfloat/multilingual-e5-small'
            ))
            self.store = PostgresVectorStore(self.settings.database_url, embedder=embedder)
        else:
            self.store = SQLiteKBStore(self.settings.database_path)
        self.generator = generator or GeminiClient(
            self.settings.gemini_api_key,
            self.settings.gemini_model,
        )
        self.meta = meta_client or MetaClient(self.settings)
        self.chat = ChatService(self.store, self.settings, self.generator)
        self.limiter = RateLimiter(self.settings.max_requests_per_minute)
        try:
            tenant_id = getattr(self.settings, 'tenant_id', 'neuralvillage')
            refresh_from_files(
                self.store,
                self.settings.kb_path,
                self.settings.approved_paths_path,
                tenant_id=tenant_id,
                scope_id='public',
            )
            refresh_workflow_library(
                self.store,
                Path(__file__).with_name('knowledge') / 'neuralspace_workflows.json',
                tenant_id=tenant_id,
                scope_id='public',
            )
        except ValueError:
            logger.warning("knowledge_refresh_failed", exc_info=True)

    def __call__(self, environ, start_response):
        method = environ.get('REQUEST_METHOD', 'GET').upper()
        path = environ.get('PATH_INFO', '/')
        origin = environ.get('HTTP_ORIGIN', '').rstrip('/')

        if path == '/api/chat' and method == 'OPTIONS':
            return self._respond(start_response, '204 No Content', b'', origin=origin)
        if path == '/health' and method == 'GET':
            return self._json(
                start_response,
                '200 OK',
                {'ok': True, 'gemini_configured': bool(self.settings.gemini_api_key)},
                origin=origin,
            )
        if path == '/demo' and method == 'GET':
            try:
                body = Path(__file__).with_name('demo.html').read_bytes()
            except OSError:
                return self._json(start_response, '404 Not Found', {'error': 'Not found.'})
            return self._respond(
                start_response,
                '200 OK',
                body,
                content_type='text/html; charset=utf-8',
                origin=origin,
            )
        if path == '/widget.js' and method == 'GET':
            try:
                body = Path(__file__).with_name('widget.js').read_bytes()
            except OSError:
                return self._json(start_response, '404 Not Found', {'error': 'Not found.'})
            return self._respond(
                start_response,
                '200 OK',
                body,
                content_type='application/javascript; charset=utf-8',
                origin=origin,
            )
        if path == '/api/chat' and method == 'POST':
            if origin not in self.settings.allowed_origins:
                return self._json(start_response, '403 Forbidden', {'error': 'Origin not allowed.'})
            if not self._rate_allowed(environ, 'web'):
                return self._json(start_response, '429 Too Many Requests', {'error': 'Rate limit reached.'}, origin=origin)
            body = self._read_body(environ, self.settings.max_input_chars * 4 + 2048)
            if body is None:
                return self._json(start_response, '413 Payload Too Large', {'error': 'Request too large.'}, origin=origin)
            try:
                payload = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(start_response, '400 Bad Request', {'error': 'Invalid JSON.'}, origin=origin)
            if not isinstance(payload, dict) or not isinstance(payload.get('message'), str):
                return self._json(start_response, '400 Bad Request', {'error': 'message is required.'}, origin=origin)
            history = payload.get('history', [])
            if not isinstance(history, list):
                return self._json(start_response, '400 Bad Request', {'error': 'history must be an array.'}, origin=origin)
            if any(
                not isinstance(turn, dict)
                or turn.get('role') not in ('user', 'assistant')
                or not isinstance(turn.get('content'), str)
                or len(turn['content']) > 800
                for turn in history[-6:]
            ):
                return self._json(start_response, '400 Bad Request', {'error': 'history contains an invalid turn.'}, origin=origin)
            tenant_id, scope_id = self._web_scope(origin)
            result = self.chat.answer(
                payload['message'],
                history=history[-6:],
                tenant_id=tenant_id,
                scope_id=scope_id,
            )
            return self._json(start_response, '200 OK', result, origin=origin)
        if path == '/api/feedback' and method == 'POST':
            if origin not in self.settings.allowed_origins:
                return self._json(start_response, '403 Forbidden', {'error': 'Origin not allowed.'})
            if not self._rate_allowed(environ, 'feedback'):
                return self._json(start_response, '429 Too Many Requests', {'error': 'Rate limit reached.'}, origin=origin)
            body = self._read_body(environ, 4096)
            if body is None:
                return self._json(start_response, '413 Payload Too Large', {'error': 'Request too large.'}, origin=origin)
            try:
                payload = json.loads(body.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                return self._json(start_response, '400 Bad Request', {'error': 'Invalid JSON.'}, origin=origin)
            rating = payload.get('rating') if isinstance(payload, dict) else None
            paths = payload.get('source_paths', []) if isinstance(payload, dict) else []
            correction = payload.get('correction', '') if isinstance(payload, dict) else ''
            if type(rating) is not int or rating not in (-1, 1):
                return self._json(start_response, '400 Bad Request', {'error': 'rating must be -1 or 1.'}, origin=origin)
            if not isinstance(paths, list) or len(paths) > 10 or not all(isinstance(item, str) and len(item) <= 200 for item in paths):
                return self._json(start_response, '400 Bad Request', {'error': 'source_paths must be a short string list.'}, origin=origin)
            if not isinstance(correction, str) or len(correction) > 1000:
                return self._json(start_response, '400 Bad Request', {'error': 'correction is too long.'}, origin=origin)
            if correction and (
                PERSONAL_DATA.search(correction)
                or PRIVATE_REFERENCE.search(correction)
                or MEDICAL_REQUEST.search(correction)
                or any(pattern.search(correction) for pattern in SECRET_PATTERNS)
            ):
                return self._json(start_response, '400 Bad Request', {'error': 'Do not submit personal, medical, or secret information.'}, origin=origin)
            tenant_id, scope_id = self._web_scope(origin)
            try:
                feedback_id = self.store.add_feedback(
                    tenant_id, scope_id, paths, rating, correction.strip() or None
                )
            except Exception:
                return self._json(start_response, '503 Service Unavailable', {'error': 'Feedback is temporarily unavailable.'}, origin=origin)
            return self._json(start_response, '202 Accepted', {'received': True, 'feedback_id': feedback_id}, origin=origin)
        if path == '/admin/feedback' and method == 'GET':
            if not self._admin_authorized(environ):
                return self._json(start_response, '404 Not Found', {'error': 'Not found.'})
            query = parse_qs(environ.get('QUERY_STRING', ''))
            tenant_id = query.get('tenant_id', [getattr(self.settings, 'tenant_id', 'neuralvillage')])[0]
            scope_id = query.get('scope_id', ['public'])[0]
            try:
                pending = self.store.list_feedback(tenant_id, scope_id)
            except Exception:
                return self._json(start_response, '503 Service Unavailable', {'error': 'Feedback is temporarily unavailable.'})
            return self._json(start_response, '200 OK', {'items': pending})
        if path == '/admin/feedback/approve' and method == 'POST':
            if not self._admin_authorized(environ):
                return self._json(start_response, '404 Not Found', {'error': 'Not found.'})
            payload = self._read_json(environ, 4096)
            if payload is None:
                return self._json(start_response, '400 Bad Request', {'error': 'Invalid JSON.'})
            feedback_id = payload.get('feedback_id')
            tenant_id = payload.get('tenant_id')
            scope_id = payload.get('scope_id')
            if type(feedback_id) is not int or not self._valid_scope_id(tenant_id) or not self._valid_scope_id(scope_id):
                return self._json(start_response, '400 Bad Request', {'error': 'feedback_id, tenant_id, and scope_id are required.'})
            try:
                approved = self.store.approve_feedback(feedback_id, tenant_id, scope_id)
            except Exception:
                return self._json(start_response, '503 Service Unavailable', {'error': 'Feedback review failed.'})
            return self._json(start_response, '200 OK', {'approved': approved})
        if path == '/admin/knowledge' and method == 'POST':
            if not self._admin_authorized(environ):
                return self._json(start_response, '404 Not Found', {'error': 'Not found.'})
            payload = self._read_json(environ, 256 * 1024)
            if payload is None:
                return self._json(start_response, '400 Bad Request', {'error': 'Invalid JSON.'})
            tenant_id = payload.get('tenant_id')
            scope_id = payload.get('scope_id')
            items = payload.get('documents')
            if not self._valid_scope_id(tenant_id) or not self._valid_scope_id(scope_id):
                return self._json(start_response, '400 Bad Request', {'error': 'Valid tenant_id and scope_id are required.'})
            if not isinstance(items, list) or not 1 <= len(items) <= 100:
                return self._json(start_response, '400 Bad Request', {'error': 'documents must contain 1 to 100 approved facts.'})
            documents = []
            for item in items:
                if not isinstance(item, dict):
                    return self._json(start_response, '400 Bad Request', {'error': 'Invalid document.'})
                source_path = item.get('path')
                content = item.get('content')
                if not isinstance(source_path, str) or not isinstance(content, str) or not source_path.strip() or not content.strip() or len(content) > 8000:
                    return self._json(start_response, '400 Bad Request', {'error': 'Each document needs a path and content under 8000 characters.'})
                if any(pattern.search(content) for pattern in SECRET_PATTERNS):
                    return self._json(start_response, '400 Bad Request', {'error': 'Secret-like content cannot be indexed.'})
                clean_path = source_path.strip()[:200]
                clean_content = content.strip()
                documents.append({
                    'doc_id': hashlib.sha256(f'{tenant_id}\0{scope_id}\0{clean_path}\0{clean_content}'.encode('utf-8')).hexdigest(),
                    'path': clean_path,
                    'content': clean_content,
                })
            try:
                self.store.upsert_documents(documents, tenant_id=tenant_id, scope_id=scope_id)
            except Exception:
                return self._json(start_response, '503 Service Unavailable', {'error': 'Knowledge ingestion failed.'})
            return self._json(start_response, '200 OK', {'indexed': len(documents), 'tenant_id': tenant_id, 'scope_id': scope_id})
        if path in ('/admin/reindex', '/admin/status'):
            if method != 'POST' and path.endswith('/reindex'):
                return self._json(start_response, '405 Method Not Allowed', {'error': 'POST required.'})
            if not self._admin_authorized(environ):
                return self._json(start_response, '404 Not Found', {'error': 'Not found.'})
            if path.endswith('/reindex'):
                try:
                    result = refresh_from_files(
                        self.store,
                        self.settings.kb_path,
                        self.settings.approved_paths_path,
                    )
                except ValueError:
                    return self._json(start_response, '400 Bad Request', {'error': 'KB refresh failed.'})
                return self._json(start_response, '200 OK', result)
            today = time.strftime('%Y-%m-%d', time.gmtime())
            return self._json(start_response, '200 OK', self.store.stats(today))
        if path == '/webhooks/meta':
            if method == 'GET':
                return self._meta_verification(environ, start_response)
            if method == 'POST':
                return self._meta_webhook(environ, start_response)
        return self._json(start_response, '404 Not Found', {'error': 'Not found.'})

    def _meta_verification(self, environ, start_response):
        query = parse_qs(environ.get('QUERY_STRING', ''))
        mode = query.get('hub.mode', [''])[0]
        token = query.get('hub.verify_token', [''])[0]
        challenge = query.get('hub.challenge', [''])[0]
        if (
            mode == 'subscribe'
            and self.settings.meta_verify_token
            and hmac.compare_digest(token, self.settings.meta_verify_token)
        ):
            return self._respond(start_response, '200 OK', challenge.encode('utf-8'), 'text/plain; charset=utf-8')
        return self._respond(start_response, '403 Forbidden', b'Forbidden', 'text/plain; charset=utf-8')

    def _meta_webhook(self, environ, start_response):
        body = self._read_body(environ, 1024 * 128)
        signature = environ.get('HTTP_X_HUB_SIGNATURE_256', '')
        if body is None:
            return self._respond(start_response, '413 Payload Too Large', b'Payload too large.')
        if not verify_signature(body, signature, self.settings.meta_app_secret):
            return self._respond(start_response, '403 Forbidden', b'Forbidden')
        try:
            payload = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._respond(start_response, '400 Bad Request', b'Invalid JSON.')
        for event in extract_message_events(payload):
            channel = event['channel']
            recipient = event['recipient']
            message = event['text']
            if not self._rate_allowed(environ, f'meta:{channel}:{recipient}'):
                continue
            tenant_id, scope_id = self._channel_scope(channel, event['integration_id'])
            result = self.chat.answer(message, tenant_id=tenant_id, scope_id=scope_id)
            try:
                self.meta.send_text(channel, recipient, result['answer'])
            except RuntimeError:
                continue
        return self._respond(start_response, '200 OK', b'OK')

    def _scope_pair(self, mapping, key):
        value = mapping.get(key)
        if isinstance(value, str):
            return getattr(self.settings, 'tenant_id', 'neuralvillage'), value
        if isinstance(value, dict):
            tenant_id = value.get('tenant_id', getattr(self.settings, 'tenant_id', 'neuralvillage'))
            scope_id = value.get('scope_id', 'public')
            if isinstance(tenant_id, str) and isinstance(scope_id, str):
                return tenant_id, scope_id
        return getattr(self.settings, 'tenant_id', 'neuralvillage'), 'public'

    def _web_scope(self, origin):
        mapping = getattr(self.settings, 'web_scope_map', {})
        return self._scope_pair(mapping, origin)

    def _channel_scope(self, channel, integration_id):
        mapping = getattr(self.settings, 'channel_scope_map', {})
        return self._scope_pair(mapping, f'{channel}:{integration_id}')

    def _rate_allowed(self, environ, suffix):
        address = environ.get('REMOTE_ADDR', '')
        return self.limiter.allow(f'{address}:{suffix}')

    def _admin_authorized(self, environ):
        token = self.settings.admin_token
        authorization = environ.get('HTTP_AUTHORIZATION', '')
        return bool(token) and hmac.compare_digest(authorization, f'Bearer {token}')

    @staticmethod
    def _read_body(environ, max_bytes):
        try:
            length = int(environ.get('CONTENT_LENGTH') or 0)
        except ValueError:
            return None
        if length < 0 or length > max_bytes:
            return None
        return environ['wsgi.input'].read(length)

    @classmethod
    def _read_json(cls, environ, max_bytes):
        body = cls._read_body(environ, max_bytes)
        if body is None:
            return None
        try:
            payload = json.loads(body.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _valid_scope_id(value):
        return isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,119}', value) is not None

    def _json(self, start_response, status, payload, origin=''):
        body = json.dumps(payload, ensure_ascii=True).encode('utf-8')
        return self._respond(
            start_response,
            status,
            body,
            'application/json; charset=utf-8',
            origin,
        )

    def _respond(self, start_response, status, body, content_type='text/plain; charset=utf-8', origin=''):
        headers = [
            ('Content-Type', content_type),
            ('Content-Length', str(len(body))),
            ('Cache-Control', 'no-store'),
            ('X-Content-Type-Options', 'nosniff'),
        ]
        if origin and origin in self.settings.allowed_origins:
            headers.extend([
                ('Access-Control-Allow-Origin', origin),
                ('Vary', 'Origin'),
                ('Access-Control-Allow-Methods', 'POST, OPTIONS'),
                ('Access-Control-Allow-Headers', 'Content-Type'),
            ])
        start_response(status, headers)
        return [body]


application = SupportBotApplication()

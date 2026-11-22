import os
import json
from dataclasses import dataclass
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
ENV_FILE_KEYS = {
    'GEMINI_API_KEY', 'GEMINI_MODEL', 'GEMINI_DAILY_CALL_LIMIT',
    'ANSWER_CACHE_TTL_SECONDS', 'ALLOWED_ORIGINS', 'MAX_CHAT_INPUT_CHARS',
    'MAX_REQUESTS_PER_MINUTE', 'SUPPORTBOT_ADMIN_TOKEN', 'SUPPORTBOT_DB',
    'KB_PATH', 'APPROVED_PATHS_PATH', 'META_APP_SECRET', 'META_VERIFY_TOKEN',
    'META_ACCESS_TOKEN', 'META_GRAPH_VERSION', 'WHATSAPP_PHONE_NUMBER_ID',
    'MESSENGER_PAGE_ID', 'INSTAGRAM_USER_ID',
    'DATABASE_URL', 'LADI_TENANT_ID', 'DEFAULT_KB_SCOPE', 'EMBEDDING_MODEL',
    'WEB_SCOPE_MAP', 'CHANNEL_SCOPE_MAP',
}


def load_env_files(environ=None, root_dir=ROOT_DIR, package_dir=PACKAGE_DIR):
    """Load supported keys from private .env files; never read .env.example."""
    target = os.environ if environ is None else environ
    process_keys = set(target)
    loaded = {}
    for path in (Path(root_dir) / '.env', Path(package_dir) / '.env'):
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except OSError:
            continue
        for line in lines:
            entry = line.strip()
            if not entry or entry.startswith('#'):
                continue
            if entry.startswith('export '):
                entry = entry[7:].lstrip()
            key, separator, value = entry.partition('=')
            key = key.strip()
            if not separator or key not in ENV_FILE_KEYS or key in process_keys:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            elif ' #' in value:
                value = value.split(' #', 1)[0].rstrip()
            loaded[key] = value
    target.update(loaded)
    return target


load_env_files()


def _integer(name, default):
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return int(default)


def _json_mapping(name):
    raw = os.environ.get(name, '').strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f'{name} must be a JSON object.') from exc
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be a JSON object.')
    return value


@dataclass(frozen=True)
class Settings:
    kb_path: Path
    approved_paths_path: Path
    database_path: Path
    database_url: str
    tenant_id: str
    default_scope_id: str
    embedding_model: str
    web_scope_map: dict
    channel_scope_map: dict
    gemini_api_key: str
    gemini_model: str
    gemini_daily_call_limit: int
    cache_ttl_seconds: int
    max_input_chars: int
    max_requests_per_minute: int
    allowed_origins: frozenset
    admin_token: str
    meta_app_secret: str
    meta_verify_token: str
    meta_access_token: str
    meta_graph_version: str
    whatsapp_phone_number_id: str
    messenger_page_id: str
    instagram_user_id: str

    @classmethod
    def from_env(cls):
        origins = os.environ.get(
            'ALLOWED_ORIGINS',
            'http://localhost:8000,http://127.0.0.1:8000,http://localhost:9002,'
            'http://127.0.0.1:8080,http://localhost:8080',
        )
        return cls(
            kb_path=Path(os.environ.get('KB_PATH', ROOT_DIR / 'u.json')).resolve(),
            approved_paths_path=Path(
                os.environ.get('APPROVED_PATHS_PATH', PACKAGE_DIR / 'approved_paths.json')
            ).resolve(),
            database_path=Path(
                os.environ.get('SUPPORTBOT_DB', PACKAGE_DIR / 'var' / 'supportbot.sqlite3')
            ).resolve(),
            database_url=os.environ.get('DATABASE_URL', '').strip(),
            tenant_id=os.environ.get('LADI_TENANT_ID', 'neuralvillage').strip(),
            default_scope_id=os.environ.get('DEFAULT_KB_SCOPE', 'public').strip(),
            embedding_model=os.environ.get(
                'EMBEDDING_MODEL', 'intfloat/multilingual-e5-small'
            ).strip(),
            web_scope_map=_json_mapping('WEB_SCOPE_MAP'),
            channel_scope_map=_json_mapping('CHANNEL_SCOPE_MAP'),
            gemini_api_key=os.environ.get('GEMINI_API_KEY', '').strip(),
            gemini_model=os.environ.get('GEMINI_MODEL', 'gemini-3-flash-preview').strip(),
            gemini_daily_call_limit=max(0, _integer('GEMINI_DAILY_CALL_LIMIT', 200)),
            cache_ttl_seconds=max(0, _integer('ANSWER_CACHE_TTL_SECONDS', 86400)),
            max_input_chars=max(100, _integer('MAX_CHAT_INPUT_CHARS', 2000)),
            max_requests_per_minute=max(1, _integer('MAX_REQUESTS_PER_MINUTE', 30)),
            allowed_origins=frozenset(
                origin.strip().rstrip('/')
                for origin in origins.split(',')
                if origin.strip()
            ),
            admin_token=os.environ.get('SUPPORTBOT_ADMIN_TOKEN', '').strip(),
            meta_app_secret=os.environ.get('META_APP_SECRET', '').strip(),
            meta_verify_token=os.environ.get('META_VERIFY_TOKEN', '').strip(),
            meta_access_token=os.environ.get('META_ACCESS_TOKEN', '').strip(),
            meta_graph_version=os.environ.get('META_GRAPH_VERSION', 'v24.0').strip(),
            whatsapp_phone_number_id=os.environ.get('WHATSAPP_PHONE_NUMBER_ID', '').strip(),
            messenger_page_id=os.environ.get('MESSENGER_PAGE_ID', '').strip(),
            instagram_user_id=os.environ.get('INSTAGRAM_USER_ID', '').strip(),
        )

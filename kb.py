import hashlib
import json
import re
import sqlite3
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

BLOCKED_ROOTS = {
    'kb_meta',
    'customer_support_ai_safety',
    'repository_verified_facts',
    'leadership_and_team',
    'payments_and_commercial_policies',
    'careers_and_community_involvement',
    'booking_and_lead_capture',
    'testimonials_and_case_studies',
    'impact_metrics_and_traction',
    'research_and_data_initiatives',
    'announcements_and_time_sensitive_info',
}

BLOCKED_PATHS = {
    'chatbot_persona_and_behavior.claims_requiring_human_approval',
    'company_identity.registration_number_cac',
    'company_identity.tax_id_tin',
    'company_identity.regulatory_approvals',
}

SECRET_PATTERNS = (
    re.compile(r'-----BEGIN [^-]*PRIVATE KEY-----', re.IGNORECASE),
    re.compile(
        r'\b(?:api[_-]?key|secret[_-]?key|password|access[_-]?token|'
        r'client[_-]?secret|database[_-]?url)\s*[:=]\s*\S+',
        re.IGNORECASE,
    ),
    re.compile(r'\bBearer\s+\S+', re.IGNORECASE),
    re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'),
    re.compile(r'\bsk-[A-Za-z0-9]{20,}\b'),
)


def load_approved_paths(paths_file):
    try:
        paths = json.loads(Path(paths_file).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('Could not read the approved KB path list.') from exc
    if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
        raise ValueError('The approved KB path list must be a JSON array of strings.')
    return paths


def _get_path(data, dotted_path):
    value = data
    parts = re.findall(r'[^.\[\]]+|\[\d+\]', dotted_path)
    for part in parts:
        if part.startswith('['):
            if not isinstance(value, list):
                return None
            index = int(part[1:-1])
            if index >= len(value):
                return None
            value = value[index]
        else:
            if not isinstance(value, dict) or part not in value:
                return None
            value = value[part]
    return value


def _is_blocked(path):
    root = path.split('.', 1)[0].split('[', 1)[0]
    return root in BLOCKED_ROOTS or path in BLOCKED_PATHS


def build_public_documents(data, approved_paths):
    documents = []
    seen = set()
    for path in approved_paths:
        if path in seen or _is_blocked(path):
            continue
        seen.add(path)
        value = _get_path(data, path)
        if not isinstance(value, str) or not value.strip():
            continue
        if any(pattern.search(value) for pattern in SECRET_PATTERNS):
            continue
        documents.append({
            'path': path,
            'content': value.strip(),
            'doc_id': hashlib.sha256(f'{path}\0{value.strip()}'.encode('utf-8')).hexdigest(),
        })
    return documents


def refresh_workflow_library(store, library_path, tenant_id='neuralvillage', scope_id='public'):
    try:
        items = json.loads(Path(library_path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('Could not read the workflow library.') from exc
    if not isinstance(items, list):
        raise ValueError('The workflow library must be a JSON array.')
    documents = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_scope = item.get('scope', 'public')
        if item_scope != 'public':
            continue
        doc_id = item.get('id')
        title = item.get('title')
        content = item.get('content')
        if not isinstance(doc_id, str) or not isinstance(title, str) or not isinstance(content, str):
            continue
        text = f'{title}: {content}'.strip()
        if any(pattern.search(text) for pattern in SECRET_PATTERNS):
            continue
        documents.append({
            'doc_id': f'workflow:{hashlib.sha256(doc_id.encode("utf-8")).hexdigest()}',
            'path': f'customer_support_navigation.{doc_id}',
            'content': text,
        })
    store.upsert_documents(documents, tenant_id=tenant_id, scope_id=scope_id)
    return {'documents': len(documents)}


def refresh_from_files(
    store,
    kb_path,
    approved_paths_path,
    tenant_id='neuralvillage',
    scope_id='public',
):
    try:
        data = json.loads(Path(kb_path).read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError('Could not read the knowledge base JSON.') from exc
    if not isinstance(data, dict):
        raise ValueError('The knowledge base root must be a JSON object.')
    paths = load_approved_paths(approved_paths_path)
    documents = build_public_documents(data, paths)
    store.replace_documents(documents, tenant_id=tenant_id, scope_id=scope_id)
    return {'documents': len(documents), 'refreshed_on': date.today().isoformat()}


class SQLiteKBStore:
    def __init__(self, database_path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self):
        connection = sqlite3.connect(str(self.database_path), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA busy_timeout = 10000')
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self):
        with self._connection() as connection:
            connection.execute(
                'CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts_v2 '
                'USING fts5(doc_id UNINDEXED, path UNINDEXED, tenant_id UNINDEXED, '
                'scope_id UNINDEXED, content, tokenize="unicode61")'
            )
            connection.execute(
                'CREATE TABLE IF NOT EXISTS answer_cache '
                '(cache_key TEXT PRIMARY KEY, answer TEXT NOT NULL, paths_json TEXT NOT NULL, expires_at INTEGER NOT NULL)'
            )
            connection.execute(
                'CREATE TABLE IF NOT EXISTS daily_model_calls '
                '(utc_day TEXT PRIMARY KEY, calls INTEGER NOT NULL)'
            )
            connection.execute(
                'CREATE TABLE IF NOT EXISTS ladi_feedback ('
                'feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, '
                'scope_id TEXT NOT NULL, source_paths TEXT NOT NULL, rating INTEGER NOT NULL '
                'CHECK (rating IN (-1, 1)), correction TEXT, status TEXT NOT NULL DEFAULT "pending", '
                'created_at INTEGER NOT NULL)'
            )
            connection.execute(
                'CREATE TABLE IF NOT EXISTS ladi_feedback ('
                'feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, tenant_id TEXT NOT NULL, '
                'scope_id TEXT NOT NULL, source_paths TEXT NOT NULL, rating INTEGER NOT NULL '
                'CHECK (rating IN (-1, 1)), correction TEXT, status TEXT NOT NULL DEFAULT "pending", '
                'created_at INTEGER NOT NULL)'
            )

    def replace_documents(self, documents, tenant_id='neuralvillage', scope_id='public'):
        with self._connection() as connection:
            connection.execute(
                'DELETE FROM kb_fts_v2 WHERE tenant_id = ? AND scope_id = ?',
                (tenant_id, scope_id),
            )
            connection.executemany(
                'INSERT INTO kb_fts_v2 (doc_id, path, tenant_id, scope_id, content) '
                'VALUES (?, ?, ?, ?, ?)',
                [
                    (doc['doc_id'], doc['path'], tenant_id, scope_id, doc['content'])
                    for doc in documents
                ],
            )

    def upsert_documents(self, documents, tenant_id='neuralvillage', scope_id='public'):
        with self._connection() as connection:
            for doc in documents:
                connection.execute(
                    'DELETE FROM kb_fts_v2 WHERE tenant_id = ? AND scope_id = ? AND doc_id = ?',
                    (tenant_id, scope_id, doc['doc_id']),
                )
            connection.executemany(
                'INSERT INTO kb_fts_v2 (doc_id, path, tenant_id, scope_id, content) '
                'VALUES (?, ?, ?, ?, ?)',
                [
                    (doc['doc_id'], doc['path'], tenant_id, scope_id, doc['content'])
                    for doc in documents
                ],
            )

    def search(self, query, limit=5, tenant_id='neuralvillage', scope_id='public'):
        tokens = re.findall(r'[^\W_]{2,}', query.lower(), flags=re.UNICODE)
        if not tokens:
            return []
        match = ' OR '.join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens[:16])
        try:
            with self._connection() as connection:
                rows = connection.execute(
                    'SELECT doc_id, path, content FROM kb_fts_v2 '
                    'WHERE kb_fts_v2 MATCH ? AND tenant_id = ? '
                    'AND scope_id IN (?, ?) ORDER BY bm25(kb_fts_v2) LIMIT ?',
                    (match, tenant_id, scope_id, 'public', limit),
                ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(row) for row in rows]

    def get_cached(self, cache_key, now):
        with self._connection() as connection:
            row = connection.execute(
                'SELECT answer, paths_json FROM answer_cache WHERE cache_key = ? AND expires_at > ?',
                (cache_key, now),
            ).fetchone()
            if row is None:
                connection.execute('DELETE FROM answer_cache WHERE expires_at <= ?', (now,))
                return None
        return {'answer': row['answer'], 'paths': json.loads(row['paths_json'])}

    def set_cached(self, cache_key, answer, paths, expires_at):
        with self._connection() as connection:
            connection.execute(
                'INSERT OR REPLACE INTO answer_cache (cache_key, answer, paths_json, expires_at) '
                'VALUES (?, ?, ?, ?)',
                (cache_key, answer, json.dumps(paths), expires_at),
            )

    def claim_model_call(self, utc_day, daily_limit):
        with self._connection() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT calls FROM daily_model_calls WHERE utc_day = ?', (utc_day,)
            ).fetchone()
            current = row['calls'] if row else 0
            if current >= daily_limit:
                return False
            connection.execute(
                'INSERT INTO daily_model_calls (utc_day, calls) VALUES (?, 1) '
                'ON CONFLICT(utc_day) DO UPDATE SET calls = calls + 1',
                (utc_day,),
            )
            return True

    def stats(self, utc_day):
        with self._connection() as connection:
            docs = connection.execute('SELECT COUNT(*) AS count FROM kb_fts_v2').fetchone()['count']
            cached = connection.execute(
                'SELECT COUNT(*) AS count FROM answer_cache WHERE expires_at > ?',
                (int(time.time()),),
            ).fetchone()['count']
            row = connection.execute(
                'SELECT calls FROM daily_model_calls WHERE utc_day = ?', (utc_day,)
            ).fetchone()
        return {'documents': docs, 'cached_answers': cached, 'model_calls_today': row['calls'] if row else 0}

    def add_feedback(self, tenant_id, scope_id, source_paths, rating, correction=None):
        with self._connection() as connection:
            cursor = connection.execute(
                'INSERT INTO ladi_feedback '
                '(tenant_id, scope_id, source_paths, rating, correction, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (tenant_id, scope_id, json.dumps(source_paths), rating, correction, int(time.time())),
            )
            return cursor.lastrowid

    def list_feedback(self, tenant_id, scope_id, limit=50):
        with self._connection() as connection:
            rows = connection.execute(
                'SELECT feedback_id, tenant_id, scope_id, source_paths, rating, correction, '
                'status, created_at FROM ladi_feedback WHERE tenant_id = ? AND scope_id = ? '
                'AND status = ? ORDER BY feedback_id DESC LIMIT ?',
                (tenant_id, scope_id, 'pending', limit),
            ).fetchall()
        result = [dict(row) for row in rows]
        for item in result:
            item['source_paths'] = json.loads(item['source_paths'])
        return result

    def approve_feedback(self, feedback_id, tenant_id, scope_id):
        with self._connection() as connection:
            row = connection.execute(
                'SELECT correction FROM ladi_feedback WHERE feedback_id = ? AND tenant_id = ? '
                'AND scope_id = ? AND status = ?',
                (feedback_id, tenant_id, scope_id, 'pending'),
            ).fetchone()
        if row is None:
            return False
        correction = row['correction']
        if correction:
            doc_id = hashlib.sha256(
                f'reviewed-feedback:{feedback_id}:{tenant_id}:{scope_id}'.encode('utf-8')
            ).hexdigest()
            self.upsert_documents([{
                'doc_id': doc_id,
                'path': 'reviewed_feedback',
                'content': correction,
            }], tenant_id=tenant_id, scope_id=scope_id)
        with self._connection() as connection:
            connection.execute(
                'UPDATE ladi_feedback SET status = ? WHERE feedback_id = ? AND tenant_id = ? '
                'AND scope_id = ? AND status = ?',
                ('approved', feedback_id, tenant_id, scope_id, 'pending'),
            )
        return True

class FastEmbedder:
    DIMENSIONS = 384

    def __init__(self, model_name='intfloat/multilingual-e5-small'):
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:
                raise RuntimeError(
                    'Install supportbot/requirements-hosted.txt to use vector search.'
                ) from exc
            self._model = TextEmbedding(model_name=self.model_name)
        return self._model

    def embed_documents(self, texts):
        model = self._load_model()
        return [
            [float(value) for value in vector.tolist()]
            for vector in model.embed([f'passage: {text}' for text in texts])
        ]

    def embed_query(self, text):
        model = self._load_model()
        vector = next(model.embed([f'query: {text}']))
        return [float(value) for value in vector.tolist()]


def _vector_literal(vector):
    return '[' + ','.join(format(value, '.8g') for value in vector) + ']'


class PostgresVectorStore:
    """One hosted Postgres database for tenant/scope-filtered vector knowledge."""

    def __init__(self, database_url, embedder=None):
        self.database_url = database_url
        self.embedder = embedder or FastEmbedder()
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise RuntimeError('Install psycopg to use hosted PostgreSQL.') from exc
        self._psycopg = psycopg
        self._dict_row = dict_row
        self.initialize()

    def _connection(self):
        return self._psycopg.connect(
            self.database_url,
            connect_timeout=8,
            row_factory=self._dict_row,
        )

    def initialize(self):
        try:
            with self._connection() as connection:
                connection.execute('CREATE EXTENSION IF NOT EXISTS vector')
                connection.execute(
                    'CREATE TABLE IF NOT EXISTS ladi_knowledge ('
                    'tenant_id TEXT NOT NULL, scope_id TEXT NOT NULL, doc_id TEXT NOT NULL, '
                    'source_path TEXT NOT NULL, content TEXT NOT NULL, embedding vector(384) NOT NULL, '
                    'search_tsv TSVECTOR GENERATED ALWAYS AS (to_tsvector(\'simple\', content)) STORED, '
                    'updated_at TIMESTAMPTZ NOT NULL DEFAULT now(), '
                    'PRIMARY KEY (tenant_id, scope_id, doc_id))'
                )
                connection.execute(
                    'CREATE INDEX IF NOT EXISTS ladi_knowledge_scope_idx '
                    'ON ladi_knowledge (tenant_id, scope_id)'
                )
                connection.execute(
                    'CREATE INDEX IF NOT EXISTS ladi_knowledge_search_idx '
                    'ON ladi_knowledge USING GIN (search_tsv)'
                )
                connection.execute(
                    'CREATE INDEX IF NOT EXISTS ladi_knowledge_embedding_idx '
                    'ON ladi_knowledge USING hnsw (embedding vector_cosine_ops)'
                )
                connection.execute(
                    'CREATE TABLE IF NOT EXISTS ladi_feedback ('
                    'feedback_id BIGSERIAL PRIMARY KEY, tenant_id TEXT NOT NULL, scope_id TEXT NOT NULL, '
                    'source_paths JSONB NOT NULL, rating SMALLINT NOT NULL CHECK (rating IN (-1, 1)), '
                    'correction TEXT, status TEXT NOT NULL DEFAULT \'pending\', '
                    'created_at TIMESTAMPTZ NOT NULL DEFAULT now())'
                )
                connection.execute(
                    'CREATE TABLE IF NOT EXISTS ladi_answer_cache ('
                    'cache_key TEXT PRIMARY KEY, answer TEXT NOT NULL, paths JSONB NOT NULL, '
                    'expires_at BIGINT NOT NULL)'
                )
                connection.execute(
                    'CREATE TABLE IF NOT EXISTS ladi_daily_model_calls ('
                    'utc_day DATE PRIMARY KEY, calls INTEGER NOT NULL)'
                )
        except Exception as exc:
            raise RuntimeError('Could not initialize hosted pgvector storage.') from exc

    def replace_documents(self, documents, tenant_id='neuralvillage', scope_id='public'):
        with self._connection() as connection:
            connection.execute(
                'DELETE FROM ladi_knowledge WHERE tenant_id = %s AND scope_id = %s',
                (tenant_id, scope_id),
            )
            self._insert_documents(connection, documents, tenant_id, scope_id)

    def upsert_documents(self, documents, tenant_id='neuralvillage', scope_id='public'):
        with self._connection() as connection:
            self._insert_documents(connection, documents, tenant_id, scope_id)

    def _insert_documents(self, connection, documents, tenant_id, scope_id):
        if not documents:
            return
        vectors = self.embedder.embed_documents([doc['content'] for doc in documents])
        connection.executemany(
            'INSERT INTO ladi_knowledge '
            '(tenant_id, scope_id, doc_id, source_path, content, embedding) '
            'VALUES (%s, %s, %s, %s, %s, %s::vector) '
            'ON CONFLICT (tenant_id, scope_id, doc_id) DO UPDATE SET '
            'source_path = EXCLUDED.source_path, content = EXCLUDED.content, '
            'embedding = EXCLUDED.embedding, updated_at = now()',
            [
                (tenant_id, scope_id, doc['doc_id'], doc['path'], doc['content'], _vector_literal(vector))
                for doc, vector in zip(documents, vectors)
            ],
        )

    def search(self, query, limit=5, tenant_id='neuralvillage', scope_id='public'):
        vector = _vector_literal(self.embedder.embed_query(query))
        scope_ids = [scope_id] if scope_id == 'public' else [scope_id, 'public']
        with self._connection() as connection:
            rows = connection.execute(
                'SELECT doc_id, source_path AS path, content, '
                '(embedding <=> %s::vector) AS distance, '
                'ts_rank_cd(search_tsv, plainto_tsquery(\'simple\', %s)) AS lexical_rank '
                'FROM ladi_knowledge WHERE tenant_id = %s AND scope_id = ANY(%s) '
                'ORDER BY (embedding <=> %s::vector) - '
                '(ts_rank_cd(search_tsv, plainto_tsquery(\'simple\', %s)) * 0.05) '
                'LIMIT %s',
                (vector, query, tenant_id, scope_ids, vector, query, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_feedback(self, tenant_id, scope_id, source_paths, rating, correction=None):
        with self._connection() as connection:
            row = connection.execute(
                'INSERT INTO ladi_feedback (tenant_id, scope_id, source_paths, rating, correction) '
                'VALUES (%s, %s, %s, %s, %s) RETURNING feedback_id',
                (tenant_id, scope_id, json.dumps(source_paths), rating, correction),
            ).fetchone()
        return row['feedback_id']

    def list_feedback(self, tenant_id, scope_id, limit=50):
        with self._connection() as connection:
            rows = connection.execute(
                'SELECT feedback_id, tenant_id, scope_id, source_paths, rating, correction, '
                'status, created_at FROM ladi_feedback WHERE tenant_id = %s AND scope_id = %s '
                'AND status = %s ORDER BY feedback_id DESC LIMIT %s',
                (tenant_id, scope_id, 'pending', limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def approve_feedback(self, feedback_id, tenant_id, scope_id):
        with self._connection() as connection:
            row = connection.execute(
                'SELECT correction FROM ladi_feedback WHERE feedback_id = %s AND tenant_id = %s '
                'AND scope_id = %s AND status = %s',
                (feedback_id, tenant_id, scope_id, 'pending'),
            ).fetchone()
        if row is None:
            return False
        correction = row['correction']
        if correction:
            doc_id = hashlib.sha256(
                f'reviewed-feedback:{feedback_id}:{tenant_id}:{scope_id}'.encode('utf-8')
            ).hexdigest()
            self.upsert_documents([{
                'doc_id': doc_id,
                'path': 'reviewed_feedback',
                'content': correction,
            }], tenant_id=tenant_id, scope_id=scope_id)
        with self._connection() as connection:
            connection.execute(
                'UPDATE ladi_feedback SET status = %s WHERE feedback_id = %s AND tenant_id = %s '
                'AND scope_id = %s AND status = %s',
                ('approved', feedback_id, tenant_id, scope_id, 'pending'),
            )
        return True

    def get_cached(self, cache_key, now):
        with self._connection() as connection:
            row = connection.execute(
                'SELECT answer, paths FROM ladi_answer_cache WHERE cache_key = %s AND expires_at > %s',
                (cache_key, now),
            ).fetchone()
            connection.execute('DELETE FROM ladi_answer_cache WHERE expires_at <= %s', (now,))
        if row is None:
            return None
        return {'answer': row['answer'], 'paths': row['paths']}

    def set_cached(self, cache_key, answer, paths, expires_at):
        with self._connection() as connection:
            connection.execute(
                'INSERT INTO ladi_answer_cache (cache_key, answer, paths, expires_at) '
                'VALUES (%s, %s, %s::jsonb, %s) ON CONFLICT (cache_key) DO UPDATE SET '
                'answer = EXCLUDED.answer, paths = EXCLUDED.paths, expires_at = EXCLUDED.expires_at',
                (cache_key, answer, json.dumps(paths), expires_at),
            )

    def claim_model_call(self, utc_day, daily_limit):
        with self._connection() as connection:
            row = connection.execute(
                'INSERT INTO ladi_daily_model_calls (utc_day, calls) VALUES (%s, 1) '
                'ON CONFLICT (utc_day) DO UPDATE SET calls = ladi_daily_model_calls.calls + 1 '
                'WHERE ladi_daily_model_calls.calls < %s RETURNING calls',
                (utc_day, daily_limit),
            ).fetchone()
        return row is not None

    def stats(self, utc_day):
        with self._connection() as connection:
            docs = connection.execute(
                'SELECT COUNT(*) AS count FROM ladi_knowledge'
            ).fetchone()['count']
            cached = connection.execute(
                'SELECT COUNT(*) AS count FROM ladi_answer_cache WHERE expires_at > %s',
                (int(time.time()),),
            ).fetchone()['count']
            row = connection.execute(
                'SELECT calls FROM ladi_daily_model_calls WHERE utc_day = %s', (utc_day,)
            ).fetchone()
        return {'documents': docs, 'cached_answers': cached, 'model_calls_today': row['calls'] if row else 0}

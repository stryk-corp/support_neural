import hashlib
import re
import time
from datetime import datetime, timezone


FALLBACK = (
    "I can't confirm that from the information approved for customer support. "
    'Please contact NeuralVillage through its published support channels.'
)
MEDICAL_REQUEST = re.compile(
    r'\b(diagnos\w*|treat\w*|prescription\w*|what condition|medical advice|'
    r'my symptoms|these symptoms|am i sick|what disease)\b',
    re.IGNORECASE,
)
PERSONAL_DATA = re.compile(
    r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|'
    r'\b(?:\+?\d[\d ()-]{7,}\d)\b|\b\d{4,}\b',
    re.IGNORECASE,
)
PRIVATE_REFERENCE = re.compile(
    r'\b(?:my|mine|our|ours)\b',
    re.IGNORECASE,
)
def _normalize_search_text(text):
    normalized = re.sub(r'[^a-z0-9]+', ' ', text.casefold()).strip()
    return re.sub(r'\bneural\s+space\b', 'neuralspace', normalized)


NAVIGATION_FALLBACKS = {
    'benchmark': (
        'To open NeuralSpace Benchmark / Brain Lab, sign in and open MySpace, then use the Benchmark link, or go to /benchmark/. '
        'Under Brain health, choose or set a state, download the CSV template, and upload a UTF-8 CSV of supplied derived scores. '
        'The importer does not analyze raw EEG or medical-imaging signals.'
    ),
    'brainlab': (
        'To take a measurement in NeuralSpace Brain Lab: 1) sign in and open MySpace > Benchmark, or go to /benchmark/; 2) open the Brain health tab; '
        '3) set or confirm the measurement state; 4) download the header-only CSV template; 5) fill recorded_at with ISO 8601 text and one row per session; '
        '6) include any supplied regional scores, with values from 0 to 100 or blank, and notes if needed; 7) choose the CSV file and upload it. '
        'The file must be UTF-8, .csv, no larger than 2 MB, and no more than 500 rows. Each row needs recorded_at and at least one numeric score. '
        'Brain Lab stores supplied derived regional scores, not raw EEG or medical-imaging signals. The displayed scan score is the mean of supplied values in that row.'
    ),
    'emr': (
        'Open /emr/ for EMR Exchange. Sign in, enter a record title and document type, and upload a PDF, PNG, JPEG, CSV, or TXT file up to 10 MB. '
        'The file is private until you explicitly list it. A clinician or researcher can request access, and approval completes NST debit/credit transfer and access granting.'
    ),
    'profile': (
        'Open /profile/ to update your name, account role, supported state, cognitive goal, BCI experience, email, data-processing consent, and optional medical-record-sharing consent. '
        'Location is state-level, not precise GPS.'
    ),
    'wellness': (
        'Open /wellness/ for browser-based cognitive tasks, saved Brain Lab measurements, and the mental-health check-in. The tasks are exploratory and not validated clinical instruments. '
        'Use Profile to set the default state if you need location context.'
    ),
    'neurohealth': (
        'Open /neurohealth/ for NeuroHealth Coverage. It combines saved scans, wellness sessions, check-ins, profile context, records, appointments, learning, protocols, and wallet activity into a descriptive review. '
        'It is not insurance, a diagnosis, or professional-care replacement.'
    ),
    'appointments': (
        'Open /appointment/ to choose a clinician, date, time, and telemedicine or in-person modality. Submission creates a requested appointment record; it does not confirm a real clinician visit or share records automatically.'
    ),
}

SMALL_TALK = re.compile(
    r'^\s*(hi|hello|hey|good (morning|afternoon|evening)|how are (you|u)\s*(doing|today)?|'
    r'how s it going|what s up|thanks?|thank you|bye|goodbye|see you)\s*[!.?]*\s*$',
    re.IGNORECASE,
)


NAVIGATION_PATTERNS = (
    (re.compile(r'\b(take|create|upload|import).{0,30}\b(measurement|scan|csv|brain lab|brainlab)\b', re.I), 'brainlab'),
    (re.compile(r'\b(measurement|scan|csv|brain lab|brainlab).{0,30}\b(take|create|upload|import|navigate|open)\b', re.I), 'brainlab'),
    (re.compile(r'\b(benchmark|brain lab|brainlab)\b', re.I), 'benchmark'),
    (re.compile(r'\b(emr|medical report|medical record|report marketplace|upload report|share report)\b', re.I), 'emr'),
    (re.compile(r'\b(profile|update profile|location state|cognitive goal|account role)\b', re.I), 'profile'),
    (re.compile(r'\b(wellness|cognitive task|brain test|stroop|go no go)\b', re.I), 'wellness'),
    (re.compile(r'\b(neurohealth coverage|coverage|insurance)\b', re.I), 'neurohealth'),
    (re.compile(r'\b(appointment|book|clinician|visit)\b', re.I), 'appointments'),
)


class ChatService:
    def __init__(self, store, settings, generator):
        self.store = store
        self.settings = settings
        self.generator = generator

    def answer(self, question, history=None, tenant_id='neuralvillage', scope_id='public'):
        question = question.strip()
        if not question or len(question) > self.settings.max_input_chars:
            return {'answer': FALLBACK, 'sources': [], 'cached': False}
        if MEDICAL_REQUEST.search(question):
            return {'answer': FALLBACK, 'sources': [], 'cached': False}

        safe_history = self._safe_history(question, history)
        navigation_answer = self._navigation_answer(question)
        if navigation_answer:
            answer, sources = navigation_answer
            cache_key = self._cache_key(question, [
                {'doc_id': f'nav:{source}', 'content': answer}
                for source in sources
            ], tenant_id, scope_id)
            now = int(time.time())
            cached = self.store.get_cached(cache_key, now)
            if cached:
                return {'answer': cached['answer'], 'sources': cached['paths'], 'cached': True}
            if self.settings.cache_ttl_seconds:
                self.store.set_cached(cache_key, answer, sources, now + self.settings.cache_ttl_seconds)
            return {'answer': answer, 'sources': sources, 'cached': False}

        if SMALL_TALK.search(question):
            documents = [{
                'doc_id': 'ladi-persona',
                'path': 'ladi.persona',
                'content': 'Ladi is the warm, conversational official AI assistant for NeuralVillage and NeuralSpace.',
            }]
        else:
            retrieval_query = _normalize_search_text(' '.join([
                *[turn['content'] for turn in safe_history if turn['role'] == 'user'][-3:],
                question,
            ]))
            documents = self.store.search(
                retrieval_query,
                tenant_id=tenant_id,
                scope_id=scope_id,
            )

        sources = list(dict.fromkeys(document['path'] for document in documents))
        cache_key = self._cache_key(question, documents, tenant_id, scope_id)
        now = int(time.time())
        cache_allowed = not history and not (
            PERSONAL_DATA.search(question) or PRIVATE_REFERENCE.search(question)
        )
        if cache_allowed:
            cached = self.store.get_cached(cache_key, now)
            if cached:
                return {'answer': cached['answer'], 'sources': cached['paths'], 'cached': True}

        if not self.settings.gemini_api_key:
            return {'answer': FALLBACK, 'sources': sources, 'cached': False}

        utc_day = datetime.now(timezone.utc).date().isoformat()
        if not self.store.claim_model_call(utc_day, self.settings.gemini_daily_call_limit):
            return {'answer': FALLBACK, 'sources': sources, 'cached': False}

        try:
            answer = self.generator.generate(question, documents, history=safe_history)
        except Exception as exc:
            if sources and all(source == 'ladi.persona' for source in sources):
                return {
                    'answer': "Hello! I'm Ladi, the official AI assistant for NeuralVillage. How can I help you explore NeuralVillage or NeuralSpace today?",
                    'sources': sources,
                    'cached': False,
                    'provider_status': 'quota_limited',
                }
            normalized = _normalize_search_text(question)
            for key, fallback in NAVIGATION_FALLBACKS.items():
                if key in normalized:
                    return {
                        'answer': fallback,
                        'sources': sources,
                        'cached': False,
                        'provider_status': 'quota_limited',
                    }
            status = getattr(exc, 'status', None)
            if status == 429:
                return {
                    'answer': 'Ladi is temporarily limited by the AI provider’s quota. Please try again shortly, or use the published support channels for immediate help.',
                    'sources': sources,
                    'cached': False,
                    'provider_status': 'quota_limited',
                }
            return {
                'answer': 'Ladi is temporarily unable to reach the AI provider. Please try again shortly, or use the published support channels for immediate help.',
                'sources': sources,
                'cached': False,
                'provider_status': 'temporarily_unavailable',
            }

        if cache_allowed and self.settings.cache_ttl_seconds:
            self.store.set_cached(
                cache_key,
                answer,
                sources,
                now + self.settings.cache_ttl_seconds,
            )
        return {'answer': answer, 'sources': sources, 'cached': False}

    @staticmethod
    def _safe_history(question, history):
        if (
            not isinstance(history, list)
            or PERSONAL_DATA.search(question)
            or PRIVATE_REFERENCE.search(question)
            or MEDICAL_REQUEST.search(question)
        ):
            return []
        safe_history = []
        for turn in history[-6:]:
            if not isinstance(turn, dict) or turn.get('role') not in ('user', 'assistant'):
                continue
            content = turn.get('content')
            if not isinstance(content, str):
                continue
            content = content.strip()[:800]
            if not content:
                continue
            if turn['role'] == 'user' and (
                PERSONAL_DATA.search(content)
                or PRIVATE_REFERENCE.search(content)
                or MEDICAL_REQUEST.search(content)
            ):
                return []
            safe_history.append({'role': turn['role'], 'content': content})
        return safe_history

    @staticmethod
    def _navigation_answer(question):
        normalized = _normalize_search_text(question)
        for pattern, key in NAVIGATION_PATTERNS:
            if pattern.search(normalized):
                fallback = NAVIGATION_FALLBACKS.get(key)
                if fallback:
                    return fallback, [f'customer_support_navigation.{key}']
        return None

    @staticmethod
    def _cache_key(question, documents, tenant_id='neuralvillage', scope_id='public'):
        material = '\n'.join([
            tenant_id,
            scope_id,
            question.casefold().strip(),
            *(f'{doc["doc_id"]}:{doc["content"]}' for doc in documents),
        ])
        return hashlib.sha256(material.encode('utf-8')).hexdigest()

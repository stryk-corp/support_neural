import hashlib
import hmac
import json
import urllib.error
import urllib.request


def verify_signature(raw_body, signature, app_secret):
    if not app_secret or not signature or not signature.startswith('sha256='):
        return False
    expected = 'sha256=' + hmac.new(
        app_secret.encode('utf-8'), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def extract_message_events(payload):
    events = []
    object_type = payload.get('object')
    if object_type == 'whatsapp_business_account':
        channel = 'whatsapp'
        for entry in payload.get('entry', []):
            for change in entry.get('changes', []):
                value = change.get('value', {})
                for message in value.get('messages', []):
                    text = message.get('text', {}).get('body', '')
                    sender = message.get('from', '')
                    if sender and text:
                        integration_id = (
                            message.get('group_id')
                            or value.get('metadata', {}).get('phone_number_id', '')
                        )
                        events.append({
                            'channel': channel,
                            'recipient': sender,
                            'text': text,
                            'integration_id': integration_id,
                        })
    else:
        channel = 'instagram' if object_type == 'instagram' else 'messenger'
        for entry in payload.get('entry', []):
            for event in entry.get('messaging', []):
                message = event.get('message', {})
                sender = event.get('sender', {}).get('id', '')
                text = message.get('text', '')
                if sender and text and not message.get('is_echo'):
                    events.append({
                        'channel': channel,
                        'recipient': sender,
                        'text': text,
                        'integration_id': entry.get('id', ''),
                    })
    return events


def extract_messages(payload):
    return [
        (event['channel'], event['recipient'], event['text'])
        for event in extract_message_events(payload)
    ]


class MetaClient:
    def __init__(self, settings, timeout=10):
        self.settings = settings
        self.timeout = timeout

    def send_text(self, channel, recipient, text):
        settings = self.settings
        if not settings.meta_access_token:
            raise RuntimeError('Meta access token is not configured.')
        if channel == 'whatsapp':
            sender_id = settings.whatsapp_phone_number_id
            payload = {
                'messaging_product': 'whatsapp',
                'to': recipient,
                'type': 'text',
                'text': {'body': text[:4000]},
            }
        elif channel == 'instagram':
            sender_id = settings.instagram_user_id
            payload = {'recipient': {'id': recipient}, 'message': {'text': text[:1000]}}
        else:
            sender_id = settings.messenger_page_id
            payload = {'recipient': {'id': recipient}, 'message': {'text': text[:1000]}}
        if not sender_id:
            raise RuntimeError('Channel sender ID is not configured.')

        url = (
            f'https://graph.facebook.com/{settings.meta_graph_version}/'
            f'{sender_id}/messages'
        )
        request = urllib.request.Request(
            url,
            data=json.dumps(payload).encode('utf-8'),
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {settings.meta_access_token}',
            },
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise RuntimeError('Meta message delivery failed.') from exc

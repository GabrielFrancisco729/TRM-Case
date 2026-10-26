"""External adapters. Defaults are offline; never post to third-party services without explicit env config.

Slack: chat.postMessage / HTTP 429 Retry-After, API ok=false handling.
Salesforce: REST SOQL query pagination, sObject PATCH/POST. Field mapping is deployment-specific.
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import requests


@dataclass
class Delivery:
    external_id: str
    status: str = 'sent'


class DeliveryError(Exception):
    def __init__(self, message: str, retry_after: int | None = None, permanent: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.permanent = permanent


def slack_configured() -> bool:
    return bool(os.getenv('SLACK_BOT_TOKEN', '').strip())


def slack_channels() -> dict:
    try:
        value = json.loads(os.getenv('SLACK_CHANNELS_JSON', '{}'))
        return value if isinstance(value, dict) else {}
    except ValueError:
        return {}


def resolve_channel(region: str | None, owner: str | None):
    mapping = slack_channels()
    if not owner or owner == 'SYS-QUEUE':
        return mapping.get('UNASSIGNED')
    return mapping.get(region) or mapping.get('UNASSIGNED')


def slack_payload(mql: dict) -> dict:
    channel = resolve_channel(mql.get('region'), mql.get('effective_owner') or mql.get('current_owner'))
    if not channel:
        # Simulation uses a logical channel until channel IDs are configured.
        channel = '#mql-unassigned' if not mql.get('effective_owner') or mql.get('effective_owner') == 'SYS-QUEUE' else f"#mql-{str(mql.get('region') or 'unknown').lower()}"
    owner = mql.get('effective_owner') or mql.get('current_owner') or 'Unassigned'
    return {
        'channel': channel,
        'text': f"New MQL {mql['mql_id']} · {mql.get('contact_name') or 'Unknown contact'} · Owner {owner}",
        'blocks': [
            {'type':'section','text':{'type':'mrkdwn','text':f"*New MQL* · `{mql['mql_id']}`\n*Contact:* {mql.get('contact_name') or 'Unknown'}\n*Company:* {mql.get('account_name') or 'Unmatched'}\n*Owner:* {owner}\n*Intent:* {mql.get('form_kind') or 'Unknown'} · *Score:* {mql.get('queue_score',0)}"}},
            {'type':'context','elements':[{'type':'mrkdwn','text':'Quillon LeadLoop · Open your assigned queue to review and act.'}]}
        ]
    }


class SlackAdapter:
    def __init__(self, token: str | None = None, timeout: float = 7):
        self.token = token if token is not None else os.getenv('SLACK_BOT_TOKEN', '')
        self.timeout = timeout

    def post(self, payload: dict) -> Delivery:
        if not self.token:
            return Delivery(external_id='demo-only', status='simulated')
        channel = payload.get('channel', '')
        if not channel or channel.startswith('#'):
            raise DeliveryError('Slack channel ID not configured for region/unassigned queue', permanent=True)
        try:
            res = requests.post('https://slack.com/api/chat.postMessage',
                                headers={'Authorization': f'Bearer {self.token}', 'Content-type':'application/json; charset=utf-8'},
                                json=payload, timeout=self.timeout)
        except requests.RequestException as exc:
            raise DeliveryError(f'Network error: {type(exc).__name__}') from exc
        if res.status_code == 429:
            try: retry = int(res.headers.get('Retry-After','60'))
            except ValueError: retry = 60
            raise DeliveryError('Slack rate-limited (429)', retry_after=retry)
        if res.status_code >= 500:
            raise DeliveryError(f'Slack server returned {res.status_code}')
        if res.status_code >= 400:
            raise DeliveryError(f'Slack rejected request HTTP {res.status_code}', permanent=True)
        try: data = res.json()
        except ValueError as exc: raise DeliveryError('Slack returned invalid JSON') from exc
        if not data.get('ok'):
            code = str(data.get('error','unknown_slack_error'))
            permanent = code in ('channel_not_found','not_in_channel','missing_scope','invalid_auth','not_authed','account_inactive','no_permission')
            raise DeliveryError(f'Slack API: {code}', permanent=permanent)
        return Delivery(external_id=data.get('ts','unknown'),status='sent')


class SalesforceAdapter:
    """Requires OAuth bearer token and verified object/field mappings for this org."""
    def __init__(self, instance_url=None, access_token=None, api_version=None, timeout=10):
        url=(instance_url if instance_url is not None else os.getenv('SF_INSTANCE_URL','')).rstrip('/')
        self.token=access_token if access_token is not None else os.getenv('SF_ACCESS_TOKEN','')
        self.version=api_version or os.getenv('SF_API_VERSION','v61.0')
        # Never allow a custom instance_url whose host isn't HTTPS.
        if url and urlparse(url).scheme!='https':
            raise ValueError('Salesforce instance URL must be HTTPS')
        self.instance_url=url
        self.timeout=timeout

    @property
    def configured(self):
        return bool(self.instance_url and self.token)

    def request(self, method, path, params=None, json_payload=None):
        if not self.configured: raise RuntimeError('Salesforce credentials not configured')
        if not path.startswith('/services/data/') or path.startswith('//'):
            raise ValueError('Invalid Salesforce API path')
        response=requests.request(method, self.instance_url+path,
                        headers={'Authorization':f'Bearer {self.token}','Content-Type':'application/json'},
                        params=params,json=json_payload,timeout=self.timeout)
        if response.status_code >= 400:
            raise DeliveryError(f'Salesforce HTTP {response.status_code}: {response.text[:250]}',
                                permanent=response.status_code in (400,401,403,404))
        if response.status_code == 204 or not response.content: return {}
        return response.json()

    def query_all(self, soql: str) -> list[dict]:
        if not soql.lstrip().upper().startswith('SELECT '):
            raise ValueError('Only read-only SELECT SOQL supported')
        path=f'/services/data/{self.version}/query'
        params={'q':soql}
        rows=[]
        while path:
            data=self.request('GET',path,params=params)
            rows.extend(data.get('records',[]))
            path=data.get('nextRecordsUrl')
            params=None
        return rows

    def get_live_mqls(self, object_api_name: str, fields: list[str], where: str='') -> list[dict]:
        # Whitelist identifiers. SOQL WHERE must be reviewed by an admin.
        import re
        ident=re.compile(r'^[A-Za-z_][A-Za-z_0-9]*$')
        if not ident.fullmatch(object_api_name) or not fields or any(not ident.fullmatch(f) for f in fields):
            raise ValueError('Invalid Salesforce object or field mapping')
        query=f'SELECT {", ".join(fields)} FROM {object_api_name}'
        if where: query+=' WHERE '+where
        return self.query_all(query)

    def patch_record(self, object_api_name: str, record_id: str, changes: dict):
        import re
        if not re.fullmatch(r'[A-Za-z_][A-Za-z_0-9]*',object_api_name): raise ValueError('Invalid object name')
        if not re.fullmatch(r'[A-Za-z0-9]{15,18}',record_id): raise ValueError('Invalid Salesforce record ID')
        return self.request('PATCH',f'/services/data/{self.version}/sobjects/{object_api_name}/{record_id}',json_payload=changes)

    def create_task(self, who_id: str, owner_id: str, subject: str, description: str):
        return self.request('POST',f'/services/data/{self.version}/sobjects/Task',json_payload={
          'WhoId':who_id,'OwnerId':owner_id,'Subject':subject,'Description':description,'Status':'Completed','Priority':'Normal'})


def sf_mapping():
    with open(Path(__file__).parent/'salesforce_mapping.example.json',encoding='utf-8') as file:
        return json.load(file)

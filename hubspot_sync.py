"""HubSpot sync for LeadFlow: dry run by default, opt-in live mode for allow-listed test portals.

Safety rules:
- Only leads a human APPROVED are synced. Pending leads never leave the machine.
- Writes only LeadFlow's own custom properties (leadflow_*) plus email/company.
  Never touches lifecyclestage, hs_lead_status or hubspotscore, so a sync cannot
  move a contact backwards in the funnel or overwrite HubSpot's own scoring.
- An AI answer is synced only when its status is OK. DISTRUSTED/INVALID answers stay local.
- Requirements text and draft emails are not sent (data minimisation).
- Live mode needs: --live, a HUBSPOT_TOKEN, the portal ID in HUBSPOT_ALLOWED_PORTALS,
  and the token's own portal must match --portal. Any mismatch stops before a write.
- Upsert by email, so re-running is idempotent; unchanged records are skipped.
Standard library only.
"""
import hashlib
import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

API = 'https://api.hubapi.com'
BATCH_LIMIT = 100  # HubSpot batch endpoints accept up to 100 inputs
GROUP = 'contactinformation'

PROPERTIES = [
    {'name': 'leadflow_lead_id', 'label': 'LeadFlow lead ID', 'type': 'string', 'fieldType': 'text'},
    {'name': 'leadflow_score', 'label': 'LeadFlow score', 'type': 'number', 'fieldType': 'number'},
    {'name': 'leadflow_tier', 'label': 'LeadFlow tier', 'type': 'enumeration', 'fieldType': 'select',
     'options': [{'label': t.title(), 'value': t, 'displayOrder': i}
                 for i, t in enumerate(('HIGH', 'MEDIUM', 'LOW'))]},
    {'name': 'leadflow_missing', 'label': 'LeadFlow missing details', 'type': 'string', 'fieldType': 'text'},
    {'name': 'leadflow_reasons', 'label': 'LeadFlow scoring reasons', 'type': 'string', 'fieldType': 'textarea'},
    {'name': 'leadflow_ai_fit', 'label': 'LeadFlow AI fit (advisory)', 'type': 'string', 'fieldType': 'text'},
    {'name': 'leadflow_review_notes', 'label': 'LeadFlow review notes', 'type': 'string', 'fieldType': 'textarea'},
]
for _p in PROPERTIES:
    _p['groupName'] = GROUP

SYNC_SCHEMA = '''CREATE TABLE IF NOT EXISTS hubspot_sync (
 lead_id TEXT PRIMARY KEY, portal_id TEXT NOT NULL, contact_id TEXT,
 payload_hash TEXT NOT NULL, synced_at TEXT NOT NULL);'''


def _props_for(row):
    lead_id, email, company, score, tier, missing, reasons, ai_status, ai_fit, review = row
    props = {
        'email': email,
        'leadflow_lead_id': lead_id,
        'leadflow_score': str(score),
        'leadflow_tier': tier,
        'leadflow_missing': ', '.join(json.loads(missing or '[]')),
        'leadflow_reasons': '; '.join(json.loads(reasons or '[]')),
        'leadflow_review_notes': '; '.join(json.loads(review or '[]'))[:5000],
    }
    if company:
        props['company'] = company           # never blank out an existing company value
    if ai_status == 'OK' and ai_fit:
        props['leadflow_ai_fit'] = ai_fit
    return props


def plan(db):
    """Build exactly what a sync would send. No network access."""
    rows = db.execute('''SELECT id,email,company,score,tier,missing,reasons,ai_status,ai_fit,review_reasons
                         FROM leads WHERE status='APPROVED' ORDER BY created_at''').fetchall()
    skipped = db.execute("SELECT COUNT(*) FROM leads WHERE status!='APPROVED'").fetchone()[0]
    inputs = [{'idProperty': 'email', 'id': r[1], 'properties': _props_for(r)} for r in rows]
    batches = [{'inputs': inputs[i:i + BATCH_LIMIT]} for i in range(0, len(inputs), BATCH_LIMIT)]
    return {
        'mode': 'dry-run',
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'summary': {'approved_to_sync': len(inputs), 'pending_not_synced': skipped,
                    'batches': len(batches)},
        'never_written': ['lifecyclestage', 'hs_lead_status', 'hubspotscore', 'requirements', 'draft'],
        'create_properties_if_missing': {'endpoint': 'POST /crm/v3/properties/contacts',
                                         'definitions': PROPERTIES},
        'upsert': {'endpoint': 'POST /crm/v3/objects/contacts/batch/upsert', 'batches': batches},
    }


def _hash(props):
    return hashlib.sha256(json.dumps(props, sort_keys=True).encode()).hexdigest()


class HubSpotClient:
    def __init__(self, token, base=API, timeout=30):
        self.token, self.base, self.timeout = token, base, timeout

    def request(self, method, path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.base + path, data=data, method=method, headers={
            'Authorization': f'Bearer {self.token}', 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as err:
            raw = err.read()
            try:
                return err.code, json.loads(raw)
            except ValueError:
                return err.code, {'message': raw[:300].decode(errors='replace')}


class SyncRefused(RuntimeError):
    pass


def push(db, client, portal_id, allowed_portals):
    """Live sync. Raises SyncRefused before any write if a guard fails."""
    portal_id = str(portal_id)
    if portal_id not in {p.strip() for p in allowed_portals if p.strip()}:
        raise SyncRefused(f'portal {portal_id} is not in HUBSPOT_ALLOWED_PORTALS')
    status, info = client.request('GET', '/account-info/v3/details')
    if status != 200:
        raise SyncRefused(f'could not verify token portal (HTTP {status}): {info.get("message", "")}')
    if str(info.get('portalId')) != portal_id:
        raise SyncRefused(f'token belongs to portal {info.get("portalId")}, not {portal_id}')

    db.execute(SYNC_SCHEMA)
    created = []
    for prop in PROPERTIES:
        status, body = client.request('GET', f"/crm/v3/properties/contacts/{prop['name']}")
        if status == 404:
            status, body = client.request('POST', '/crm/v3/properties/contacts', prop)
            if status not in (200, 201):
                hint = ' - token needs crm.schemas.contacts.write' if status in (401, 403) else ''
                raise SyncRefused(f"creating property {prop['name']} failed (HTTP {status}): {body.get('message')}{hint}")
            created.append(prop['name'])
        elif status != 200:
            hint = (' - the token is missing scopes; it needs crm.schemas.contacts.read and '
                    'crm.schemas.contacts.write (plus crm.objects.contacts.read/write)') if status in (401, 403) else ''
            raise SyncRefused(f"checking property {prop['name']} failed (HTTP {status}): "
                              f"{body.get('message', '')}{hint}")

    plan_ = plan(db)
    synced, unchanged, errors = 0, 0, []
    for batch in plan_['upsert']['batches']:
        todo = []
        for item in batch['inputs']:
            lead_id = item['properties']['leadflow_lead_id']
            prev = db.execute('SELECT payload_hash FROM hubspot_sync WHERE lead_id=? AND portal_id=?',
                              (lead_id, portal_id)).fetchone()
            if prev and prev[0] == _hash(item['properties']):
                unchanged += 1
            else:
                todo.append(item)
        if not todo:
            continue
        status, body = client.request('POST', '/crm/v3/objects/contacts/batch/upsert', {'inputs': todo})
        if status not in (200, 201, 207):
            errors.append(f'batch failed (HTTP {status}): {body.get("message", "")}')
            continue
        by_email = {r.get('properties', {}).get('email', '').lower(): r.get('id')
                    for r in body.get('results', [])}
        for e in body.get('errors', []) or []:
            errors.append(e.get('message', 'unknown error'))
        now = datetime.now(timezone.utc).isoformat()
        with db:
            for item in todo:
                cid = by_email.get(item['id'].lower())
                if cid is None:
                    continue  # not confirmed by HubSpot: do not record as synced
                db.execute('''INSERT INTO hubspot_sync (lead_id,portal_id,contact_id,payload_hash,synced_at)
                              VALUES (?,?,?,?,?) ON CONFLICT(lead_id) DO UPDATE SET portal_id=excluded.portal_id,
                              contact_id=excluded.contact_id, payload_hash=excluded.payload_hash,
                              synced_at=excluded.synced_at''',
                           (item['properties']['leadflow_lead_id'], portal_id, cid,
                            _hash(item['properties']), now))
                synced += 1
    return {'mode': 'live', 'portal': portal_id, 'properties_created': created,
            'synced': synced, 'unchanged_skipped': unchanged, 'errors': errors,
            'pending_not_synced': plan_['summary']['pending_not_synced']}


def allowed_from_env():
    return os.environ.get('HUBSPOT_ALLOWED_PORTALS', '').split(',')

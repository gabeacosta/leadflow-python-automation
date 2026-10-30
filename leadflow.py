"""Demo lead triage: deterministic, review-first. Optional local-AI second opinion (--ai)."""
import argparse
import csv
import hashlib
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = '''CREATE TABLE IF NOT EXISTS leads (
 id TEXT PRIMARY KEY, email TEXT NOT NULL, company TEXT NOT NULL,
 requirements TEXT NOT NULL, budget REAL, score INTEGER NOT NULL,
 tier TEXT NOT NULL, reasons TEXT NOT NULL, missing TEXT NOT NULL,
 draft TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
 created_at TEXT NOT NULL);'''

# Added for the optional AI second opinion. Existing databases are migrated in connect().
AI_COLUMNS = {'ai_status': 'TEXT', 'ai_intent': 'TEXT', 'ai_fit': 'TEXT', 'ai_confidence': 'REAL',
              'ai_summary': 'TEXT', 'ai_flags': "TEXT NOT NULL DEFAULT '[]'",
              'review_reasons': "TEXT NOT NULL DEFAULT '[]'"}


def load_dotenv(path=None):
    """Load KEY=VALUE lines from .env next to this file. Shell variables win; nothing is printed."""
    import os
    path = Path(path) if path else Path(__file__).resolve().parent / '.env'
    if not path.is_file():
        return
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def normalize_email(email):
    value = str(email or '').strip().lower()
    if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', value):
        raise ValueError('Valid email is required')
    return value


def qualify(lead):
    email = normalize_email(lead.get('email'))
    company = str(lead.get('company') or '').strip()
    requirements = str(lead.get('requirements') or '').strip()
    budget_raw = lead.get('budget')
    budget = None if budget_raw in ('', None) else float(budget_raw)
    if budget is not None and (budget < 0 or budget > 1e9):
        raise ValueError('Budget out of accepted demo range')
    score, reasons, missing = 0, [], []
    if company:
        score += 15; reasons.append('Company supplied (+15)')
    else: missing.append('company')
    if len(requirements) >= 30:
        score += 35; reasons.append('Detailed requirements (+35)')
    elif requirements:
        score += 15; reasons.append('Partial requirements (+15)')
    else: missing.append('requirements')
    if budget is None:
        missing.append('budget')
    elif budget >= 5000:
        score += 40; reasons.append('Budget at least $5,000 (+40)')
    elif budget >= 1000:
        score += 20; reasons.append('Budget $1,000–$4,999 (+20)')
    else:
        reasons.append('Budget below $1,000 (+0)')
    if re.search(r'\b(urgent|asap|this week)\b', requirements, re.I):
        score += 10; reasons.append('Urgency stated (+10)')
    tier = 'HIGH' if score >= 70 else 'MEDIUM' if score >= 35 else 'LOW'
    name = str(lead.get('name') or '').strip() or 'there'
    ask = (' Could you also share ' + ', '.join(missing) + '?' ) if missing else ''
    draft = (f'Hi {name},\n\nThanks for reaching out about your project. '
             f'We received your inquiry and will review the details with our team.{ask}\n\nBest,\nTeam')
    return dict(email=email, company=company, requirements=requirements, budget=budget,
                score=score, tier=tier, reasons=reasons, missing=missing, draft=draft)


def connect(db_path):
    db = sqlite3.connect(db_path)
    db.execute(SCHEMA)
    have = {row[1] for row in db.execute('PRAGMA table_info(leads)')}
    for name, decl in AI_COLUMNS.items():
        if name not in have:
            db.execute(f'ALTER TABLE leads ADD COLUMN {name} {decl}')
    db.commit()
    return db


def submit(db, lead, ai_client=None):
    """Score with rules; if ai_client is given, attach an advisory AI assessment.

    The AI never changes score/tier and never removes a lead; it can only add review reasons.
    """
    result = qualify(lead)
    key = hashlib.sha256(result['email'].encode()).hexdigest()[:16]
    existing = db.execute('SELECT id FROM leads WHERE id=?', (key,)).fetchone()
    if existing: return {'id': key, 'created': False, **result}
    ai = None
    if ai_client is not None:
        from ai_assist import assess
        ai = assess(lead, result['tier'], ai_client)
        result['ai'] = ai
    with db:
        cur = db.execute('''INSERT OR IGNORE INTO leads
          (id,email,company,requirements,budget,score,tier,reasons,missing,draft,status,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
          (key, result['email'], result['company'], result['requirements'],result['budget'],
           result['score'],result['tier'],json.dumps(result['reasons']),
           json.dumps(result['missing']),result['draft'],'PENDING_REVIEW',
           datetime.now(timezone.utc).isoformat()))
        if cur.rowcount == 0:  # lost a race with a concurrent submit of the same email
            result.pop('ai', None)
            return {'id': key, 'created': False, **result}
        if ai is not None:
            db.execute('''UPDATE leads SET ai_status=?, ai_intent=?, ai_fit=?, ai_confidence=?,
              ai_summary=?, ai_flags=?, review_reasons=? WHERE id=?''',
              (ai['ai_status'], ai['ai_intent'], ai['ai_fit'], ai['ai_confidence'], ai['ai_summary'],
               json.dumps(ai['ai_flags']), json.dumps(ai['review_reasons']), key))
    return {'id': key, 'created': True, **result}


def approve(db, lead_id):
    with db:
        cur = db.execute("UPDATE leads SET status='APPROVED' WHERE id=? AND status='PENDING_REVIEW'", (lead_id,))
    if cur.rowcount != 1: raise ValueError('Lead not found or already reviewed')


def export(db, output):
    with open(output, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['id','email','company','budget','score','tier','status','missing','reasons',
                         'ai_fit','ai_confidence','review_reasons'])
        writer.writerows(db.execute('SELECT id,email,company,budget,score,tier,status,missing,reasons,'
                                    'ai_fit,ai_confidence,review_reasons FROM leads ORDER BY created_at'))


def main():
    parser = argparse.ArgumentParser(description='LeadFlow demonstrator — never sends emails')
    parser.add_argument('--db', default='leadflow.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('submit'); s.add_argument('json_file')
    s.add_argument('--ai', action='store_true', help='add an advisory second opinion from a local Ollama model')
    s.add_argument('--model', default=None, help='Ollama model name (default: $LEADFLOW_MODEL or qwen3)')
    s.add_argument('--ollama-url', default=None, help='default: $LEADFLOW_OLLAMA_URL or http://localhost:11434')
    s.add_argument('--ai-timeout', type=float, default=None, help='seconds to wait for the model (default 120)')
    a = sub.add_parser('approve'); a.add_argument('lead_id')
    e = sub.add_parser('export'); e.add_argument('csv_file')
    sub.add_parser('list')
    h = sub.add_parser('hubspot', help='sync APPROVED leads to HubSpot (dry run unless --live)')
    h.add_argument('action', choices=['plan', 'push'])
    h.add_argument('--out', default=None, help='plan: write the dry-run payload to this JSON file')
    h.add_argument('--live', action='store_true', help='push: actually write to HubSpot')
    h.add_argument('--portal', default=None, help='push: HubSpot portal ID (must be in HUBSPOT_ALLOWED_PORTALS)')
    args = parser.parse_args()
    load_dotenv()
    db = connect(args.db)
    if args.command == 'submit':
        client = None
        if args.ai:
            from ai_assist import OllamaClient
            client = OllamaClient(model=args.model, url=args.ollama_url, timeout=args.ai_timeout)
        print(json.dumps(submit(db,json.loads(Path(args.json_file).read_text()),ai_client=client),indent=2))
    elif args.command == 'approve':
        approve(db,args.lead_id); print('Approved for human follow-up. No message sent.')
    elif args.command == 'export':
        export(db,args.csv_file); print('Exported',args.csv_file)
    elif args.command == 'hubspot':
        import os
        import hubspot_sync as hs
        if args.action == 'plan' or not args.live:
            result = hs.plan(db)
            if args.action == 'push':
                result['note'] = 'push without --live is a dry run; nothing was sent'
            text = json.dumps(result, indent=2)
            if args.out:
                Path(args.out).write_text(text); print(f"Dry run written to {args.out}:", json.dumps(result['summary']))
            else:
                print(text)
        else:
            token = os.environ.get('HUBSPOT_TOKEN')
            if not token or not args.portal:
                raise SystemExit('Live push needs HUBSPOT_TOKEN in the environment and --portal ID')
            try:
                print(json.dumps(hs.push(db, hs.HubSpotClient(token), args.portal, hs.allowed_from_env()), indent=2))
            except hs.SyncRefused as exc:
                raise SystemExit(f'Refused, nothing written: {exc}')
    elif args.command == 'list':
        for *row, reasons in db.execute('SELECT id,email,tier,score,status,review_reasons FROM leads'):
            flags = json.loads(reasons or '[]')
            print(*row, *(['REVIEW: ' + '; '.join(flags)] if flags else []), sep=' | ')

if __name__ == '__main__': main()

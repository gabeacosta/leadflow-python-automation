"""Demo lead triage: deterministic, review-first, no external APIs required."""
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
    return db


def submit(db, lead):
    result = qualify(lead)
    key = hashlib.sha256(result['email'].encode()).hexdigest()[:16]
    with db:
        existing = db.execute('SELECT id FROM leads WHERE id=?', (key,)).fetchone()
        if existing: return {'id': key, 'created': False, **result}
        db.execute('''INSERT INTO leads
          (id,email,company,requirements,budget,score,tier,reasons,missing,draft,status,created_at)
          VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
          (key, result['email'], result['company'], result['requirements'],result['budget'],
           result['score'],result['tier'],json.dumps(result['reasons']),
           json.dumps(result['missing']),result['draft'],'PENDING_REVIEW',
           datetime.now(timezone.utc).isoformat()))
    return {'id': key, 'created': True, **result}


def approve(db, lead_id):
    with db:
        cur = db.execute("UPDATE leads SET status='APPROVED' WHERE id=? AND status='PENDING_REVIEW'", (lead_id,))
    if cur.rowcount != 1: raise ValueError('Lead not found or already reviewed')


def export(db, output):
    with open(output, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['id','email','company','budget','score','tier','status','missing','reasons'])
        writer.writerows(db.execute('SELECT id,email,company,budget,score,tier,status,missing,reasons FROM leads ORDER BY created_at'))


def main():
    parser = argparse.ArgumentParser(description='LeadFlow demonstrator — never sends emails')
    parser.add_argument('--db', default='leadflow.sqlite3')
    sub = parser.add_subparsers(dest='command', required=True)
    s = sub.add_parser('submit'); s.add_argument('json_file')
    a = sub.add_parser('approve'); a.add_argument('lead_id')
    e = sub.add_parser('export'); e.add_argument('csv_file')
    sub.add_parser('list')
    args = parser.parse_args()
    db = connect(args.db)
    if args.command == 'submit':
        print(json.dumps(submit(db,json.loads(Path(args.json_file).read_text())),indent=2))
    elif args.command == 'approve':
        approve(db,args.lead_id); print('Approved for human follow-up. No message sent.')
    elif args.command == 'export':
        export(db,args.csv_file); print('Exported',args.csv_file)
    elif args.command == 'list':
        for row in db.execute('SELECT id,email,tier,score,status FROM leads'):
            print(*row,sep=' | ')

if __name__ == '__main__': main()

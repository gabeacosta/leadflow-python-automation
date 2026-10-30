import json
import sqlite3
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from leadflow import qualify, submit, connect, approve, export, load_dotenv
import pytest

LEAD = {'name':'Jordan','email':'Jordan@Example.com','company':'Example Co',
        'requirements':'We need help building a CRM automation system this week.', 'budget':6000}

def test_scoring():
    r=qualify(LEAD)
    assert (r['score'],r['tier']) == (100,'HIGH')
    assert r['email']=='jordan@example.com'

def test_unknown_fields_not_invented():
    r=qualify({'email':'a@example.com'})
    assert set(r['missing'])=={'company','requirements','budget'}
    assert r['tier']=='LOW'

def test_invalid_input():
    with pytest.raises(ValueError): qualify({'email':'bad'})
    with pytest.raises(ValueError): qualify({'email':'a@example.com','budget':-2})

def test_duplicate_and_approval(tmp_path):
    db=connect(tmp_path/'test.sqlite3')
    first=submit(db,LEAD); second=submit(db,LEAD)
    assert first['created'] and not second['created']
    assert db.execute('select count(*) from leads').fetchone()[0]==1
    assert db.execute('select status from leads').fetchone()[0]=='PENDING_REVIEW'
    approve(db,first['id'])
    assert db.execute('select status from leads').fetchone()[0]=='APPROVED'
    with pytest.raises(ValueError): approve(db,first['id'])
    export(db,tmp_path/'out.csv')
    assert 'APPROVED' in (tmp_path/'out.csv').read_text()


def test_load_dotenv_does_not_override_shell_and_skips_blanks(tmp_path, monkeypatch):
    env = tmp_path / '.env'
    env.write_text("# comment\nLF_A='from-file'\nLF_B=from-file\nLF_EMPTY=\n")
    monkeypatch.setenv('LF_B', 'from-shell')
    monkeypatch.delenv('LF_A', raising=False); monkeypatch.delenv('LF_EMPTY', raising=False)
    load_dotenv(env)
    import os
    assert os.environ['LF_A'] == 'from-file'
    assert os.environ['LF_B'] == 'from-shell'
    assert 'LF_EMPTY' not in os.environ

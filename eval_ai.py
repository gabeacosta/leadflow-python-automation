"""Run every example lead through rules + local AI and print a comparison table.

Usage: python3 eval_ai.py [--model qwen3]. Needs Ollama running. Writes nothing to disk.
"""
import argparse
import json
from pathlib import Path

from ai_assist import OllamaClient, assess
from leadflow import qualify

parser = argparse.ArgumentParser()
parser.add_argument('--model', default=None)
args = parser.parse_args()
client = OllamaClient(model=args.model)
print(f'model: {client.model}\n')
print(f"{'example':32} {'rules':12} {'AI intent':16} {'AI fit':7} {'conf':5}  review reasons")
for path in sorted(Path('examples').glob('lead_*.json')):
    if path.stem.endswith('_output'):
        continue
    lead = json.loads(path.read_text())
    rules = qualify(lead)
    ai = assess(lead, rules['tier'], client)
    conf = '' if ai['ai_confidence'] is None else f"{ai['ai_confidence']:.2f}"
    print(f"{path.stem:32} {str(rules['score'])+' '+rules['tier']:12} {str(ai['ai_intent']):16} "
          f"{str(ai['ai_fit']):7} {conf:5}  {'; '.join(ai['review_reasons']) or '-'}")

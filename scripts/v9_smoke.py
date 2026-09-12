import json, sys
sys.stdout.reconfigure(encoding='utf-8')
data = json.loads(open('reports/v9_regression_cases.json', encoding='utf-8').read())
print(f'cases parsed: {len(data)}')
fields = {c['id'].split('-')[1] for c in data}
print(f'fields covered: {sorted(fields)}')
priorities = {}
for c in data:
    priorities[c['priority']] = priorities.get(c['priority'], 0) + 1
print(f'priority distribution: {priorities}')
sources = [c['source'] for c in data]
assert all(s.startswith('drift:') for s in sources), f'non-drift source: {sources}'
print(f'all sources are drift references: {len(sources)}/{len(sources)}')
for c in data:
    assert 'http' in c['expected'], c
    assert c['action']['kind'] == 'http'
    assert c['action']['method'] == 'POST'
print('all cases have valid http action + expected')
print('OK - V9 regression cases ready to feed into testmind run_pipeline')

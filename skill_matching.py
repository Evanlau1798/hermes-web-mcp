"""Deterministic metadata ranking; no model, skill execution or shell parsing."""
import re
from difflib import get_close_matches


CONCEPTS = {
    'merge': ('merge', 'merging', 'integrate', 'integration', '合併', '整合'),
    'upstream': ('upstream', '上游'),
    'branch': ('branch', 'branches', '分支'),
    'repository': ('repository', 'repo', '儲存庫', '倉庫'),
    'safe': ('safe', 'safely', 'safety', '安全'),
    'rebase': ('rebase', '變基'),
    'release': ('release', 'releases', '發版', '發布', '發佈'),
    'deploy': ('deploy', 'deployment', '部署'),
    'overwrite': ('overwrite', '覆寫', '覆蓋'),
    'windows': ('windows', 'powershell', '視窗'),
}
RISK = {'merge', 'rebase', 'release', 'deploy', 'overwrite'}
STOP = {'the', 'a', 'an', 'to', 'of', 'and', 'for', 'with', 'latest', 'please', 'task', 'skill', 'skills'}


def tokens(text):
    text = text.casefold()
    words = set(re.findall(r'[a-z0-9]+', text)) - STOP
    for canonical, aliases in CONCEPTS.items():
        matched = {alias for alias in aliases if alias in words or (not alias.isascii() and alias in text)}
        if matched:
            words.difference_update(aliases)
            words.add(canonical)
    return words


def rank(rows, query):
    if not query.strip():
        return rows
    wanted = tokens(query)
    ranked = []
    for row in rows:
        name = tokens(row['name'] + ' ' + row['id'].split(':')[-1])
        description = tokens(row['description'])
        exact = query.casefold() in (row['id'].casefold(), row['name'].casefold())
        matches = wanted & (name | description)
        near = {word for word in wanted - matches if len(word) > 4 and get_close_matches(word, name, n=1, cutoff=.82)}
        score = 100 * exact + 5 * len(wanted & name) + len(matches - name) + 2 * len(near)
        if score and (exact or len(wanted) <= 1 or wanted & name or len(matches) >= 2 or near):
            reason = '精確名稱／ID' if exact else '任務詞：' + ', '.join(sorted(matches | near))
            ranked.append((score, {**row, 'match_reason': reason}))
    ranked.sort(key=lambda pair: (-pair[0], pair[1]['id']))
    return [{**row, 'role': 'primary' if i == 0 else 'supplementary'} for i, (_, row) in enumerate(ranked)]


def workflow(task, rows):
    words = tokens(task)
    risks = words & RISK
    if 'merge' in risks and not words & {'upstream', 'git', 'branch', 'repository', 'repo', 'fork'}:
        risks.remove('merge')
    risks = sorted(risks)
    return {
        'task': task, 'risk': risks,
        'required_skill_ids': [rows[0]['id']] if risks and rows else [],
        'steps': ['先讀首選專用 SKILL.md 到 eof，再按內容讀必要 references；通用開發技能作補充。',
                  '確認專案 AGENTS.md、來源／目標、branch、dirty files、授權與既有證據。',
                  '依專用流程操作；結果不明先查證，保留原回執再決定下一步。'],
        'next_step': ({'tool': 'hermes_skill_read', 'skill_id': rows[0]['id']} if rows else
                      {'tool': 'hermes_skills_list', 'query': task or '填入本次具體任務'}),
    }

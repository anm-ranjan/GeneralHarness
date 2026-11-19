"""Generate and validate reviewable agent drafts. Never execute generated code on save."""
from __future__ import annotations
import json
import os
from pathlib import Path
import shutil
import threading
import uuid
from datetime import datetime, timezone
import agent_profiles

_lock = threading.RLock()
DRAFT_MAX_TOKENS = 24000
DRAFT_TIMEOUT_S = 300
DRAFT_REASONING_EFFORT = 'low'


def draft_root():
    import web_app
    return Path(web_app._DATA_DIR) / 'agent_drafts'


def draft_path(draft_id):
    if not __import__('re').fullmatch(r'draft_[a-f0-9]{24}', draft_id): raise ValueError('Invalid draft ID')
    path = draft_root() / draft_id
    if not (path / 'draft.json').is_file(): raise ValueError('Draft not found')
    return path


def read_draft(draft_id):
    return json.loads((draft_path(draft_id) / 'draft.json').read_text(encoding='utf-8'))


def safe_relative(path):
    value = Path(path)
    if value.is_absolute() or any(p in {'.', '..'} for p in value.parts) or not value.parts or '\\' in path or ':' in path or value.parts[0].startswith('.'):
        raise ValueError('Unsafe file path: ' + path)
    return value


def store_draft(payload, session_id, intent, previous=''):
    if not isinstance(payload, dict) or not agent_profiles.NAME_RE.fullmatch(str(payload.get('name', ''))): raise ValueError('Model must return a valid agent name')
    name = payload['name']; files = payload.get('files')
    if not isinstance(files, dict) or len(files) > 64 or not all(isinstance(k,str) and isinstance(v,str) for k,v in files.items()): raise ValueError('files must be a map of at most 64 text files')
    files = dict(files, **{'AGENT.md': str(payload.get('agent_md', ''))})
    if sum(len(v.encode('utf-8')) for v in files.values()) > 1000000: raise ValueError('Draft exceeds 1 MB')
    for relative in files: safe_relative(relative)
    draft_id = 'draft_' + uuid.uuid4().hex[:24]
    path = draft_root()/draft_id; folder = path/name; folder.mkdir(parents=True)
    for relative, content in files.items():
        target=folder/safe_relative(relative);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')
    profile = agent_profiles.read_profile(folder)
    errors = list(profile.errors)
    for relative, content in files.items():
        if relative.endswith('.py'):
            try: compile(content,relative,'exec')
            except SyntaxError as exc: errors.append(f'{relative}: {exc}')
    example = payload.get('example_params', {})
    if not isinstance(example, dict): errors.append('example_params must be an object')
    draft = dict(draft_id=draft_id,name=name,files=files,example_params=example,errors=errors,replaces=agent_profiles.get_profile(name) is not None,session_id=session_id,intent=intent,previous=previous,dry_run=profile.dry_run,output='')
    (path/'draft.json').write_text(json.dumps(draft,ensure_ascii=False),encoding='utf-8')
    return draft


# Bundled definitions shown to the model as worked examples.
DRAFT_EXAMPLES = ("folder-summary", "site-check")


def generate_draft(intent, session_id, previous=None, feedback=''):
    import harness_agent as agent
    import utils
    root = agent_profiles.repo_root() / 'agents'
    spec = (root / 'README.md').read_text(encoding='utf-8')
    examples = {}
    for name in DRAFT_EXAMPLES:
        for file in ('AGENT.md', 'run.py'):
            path = root / name / file
            if path.is_file():
                examples[f'{name}/{file}'] = path.read_text(encoding='utf-8')
    skills = [{'name': s.name, 'description': s.description} for s in utils.skill_registry.list_skills()]
    system = ('Create a MyHarness routine agent. Return strict JSON only: {"name":"lowercase-name","agent_md":"frontmatter and body",'
              '"files":{"run.py":"..."},"example_params":{...}}. Do not execute code or write installed agents. '
              'Use the helper API (myharness_agent). Keep the example parameters safe for a dry run.\n' + spec)
    user = json.dumps({'intent': intent, 'examples': examples, 'skills': skills, 'previous_draft': previous, 'feedback': feedback}, ensure_ascii=False)
    messages = [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]
    def request(effort):
        return agent.call_api(messages, tools=None, model=utils.WRITE_MODEL, max_tokens=DRAFT_MAX_TOKENS,
                              timeout=DRAFT_TIMEOUT_S, reasoning_effort=effort)
    response = request(DRAFT_REASONING_EFFORT)
    if not response and agent._get_api_failure() == 'status':
        # Some gateways reject the reasoning field; retry the plain request.
        response = request(None)
    if not response or not response.get('choices'):
        raise ValueError('The model request failed (' + str(agent._get_api_failure() or 'no response') + '). Check the native provider and try again.')
    choice = response['choices'][0]
    message_obj = choice.get('message') or {}
    content = message_obj.get('content', '') or ''
    try:
        payload = parse_model_json(content)
    except ValueError as exc:
        reasoning = message_obj.get('reasoning_content') or message_obj.get('reasoning') or ''
        saved = _save_failed_response(content, choice.get('finish_reason'), reasoning, response.get('usage'))
        if choice.get('finish_reason') == 'length' and not content.strip():
            message = (f'The model spent the whole {DRAFT_MAX_TOKENS}-token output limit reasoning and returned no draft. '
                       'Try again, or set models.write to a model that reasons less.')
        elif choice.get('finish_reason') == 'length':
            message = f'The draft was cut off at the {DRAFT_MAX_TOKENS}-token output limit. Ask for a smaller agent or try again.'
        elif not content.strip():
            message = 'The model returned an empty reply. Try again or rephrase the intent.'
        else:
            message = str(exc)
        raise ValueError(message + (f' Raw reply saved to {saved}.' if saved else '')) from None
    return store_draft(payload, session_id, intent, previous.get('draft_id', '') if previous else '')


def _save_failed_response(content, finish_reason, reasoning='', usage=None):
    """Keep an unparsable reply for diagnosis; never let this hide the real error."""
    try:
        folder = draft_root() / '_failed'; folder.mkdir(parents=True, exist_ok=True)
        path = folder / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '_' + uuid.uuid4().hex[:6] + '.txt')
        header = f'finish_reason: {finish_reason}\nlength: {len(content)}\nreasoning_length: {len(str(reasoning))}\nusage: {json.dumps(usage or {})}\n'
        tail = f'\n--- reasoning (last 4000 chars) ---\n{str(reasoning)[-4000:]}' if reasoning else ''
        path.write_text(f'{header}\n{content}{tail}', encoding='utf-8')
        return str(path)
    except Exception:
        return ''


def parse_model_json(content):
    """Models often wrap JSON in a Markdown fence, add a sentence around it, or put
    raw newlines inside long code strings (strict JSON forbids them)."""
    import re
    text = str(content).strip()
    candidates = [text]
    candidates += [m.group(1).strip() for m in re.finditer(r'```(?:json)?[ \t]*\n(.*?)\n```', text, re.S)]
    # A greedy fence survives inner ``` fences inside AGENT.md text.
    greedy = re.search(r'```(?:json)?[ \t]*\n(.*)\n```', text, re.S)
    if greedy: candidates.append(greedy.group(1).strip())
    decoder = json.JSONDecoder(strict=False)
    for candidate in candidates:
        try:
            value = json.loads(candidate, strict=False)
            if isinstance(value, dict): return value
        except ValueError: pass
        start = candidate.find('{')
        if start >= 0:
            try:
                value, _ = decoder.raw_decode(candidate[start:])
                if isinstance(value, dict): return value
            except ValueError: pass
    raise ValueError('The model did not return a JSON agent draft. Try again or rephrase the intent.')


def save_draft(draft_id, replace=False):
    import web_app
    with _lock:
        draft = read_draft(draft_id)
        if draft['errors']: raise ValueError('Fix draft validation errors before saving')
        source=draft_path(draft_id)/draft['name']
        # Revalidate on disk and check no links entered the draft since generation.
        if any(p.is_symlink() for p in source.rglob('*')): raise ValueError('Draft links are not allowed')
        profile=agent_profiles.read_profile(source)
        if profile.errors: raise ValueError('; '.join(profile.errors))
        # Installed into the user root under data/, which deployment syncs keep.
        root=agent_profiles.user_agents_root();root.mkdir(parents=True,exist_ok=True)
        target=root/draft['name']
        if target.is_symlink(): raise ValueError('Agent folder cannot be a symlink')
        backup=None
        # A bundled definition of the same name is shadowed, so it also needs consent.
        if (target.exists() or agent_profiles.get_profile(draft['name']) is not None) and not replace:
            raise FileExistsError('Agent exists; explicitly confirm replacement')
        if target.exists():
            backup=Path(web_app._DATA_DIR)/'agent_backups'/draft['name']/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup.parent.mkdir(parents=True,exist_ok=True);shutil.copytree(target,backup)
        staging=root/('_draft_'+uuid.uuid4().hex)
        shutil.copytree(source,staging)
        try:
            if target.exists(): shutil.rmtree(target)
            staging.rename(target)
        except Exception:
            if backup and not target.exists(): shutil.copytree(backup,target)
            raise
        return agent_profiles.read_profile(target).public(detail=True)

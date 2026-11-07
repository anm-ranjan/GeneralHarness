"""Routine-agent catalog and native run creation."""
from __future__ import annotations
import json
import os
import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
import agent_profiles
import utils
import web_app
import web_helpers
from web_models import SendMessageRequest, EventType
from session_store import AGENTS_PROJECT_ID

router = APIRouter()
_creation_lock = threading.RLock()


class StartAgentRun(BaseModel):
    text: str = Field(default='', max_length=200000)
    params: dict | None = None
    dry_run: bool = False
    working_directory: str = ''
    parent_session_id: str = ''


def _profile(name):
    profile = agent_profiles.get_profile(name)
    if profile is None: raise HTTPException(404, 'Agent not found')
    return profile


@router.get('/api/agents')
def list_agents():
    from agent_preflight import preflight
    profiles=[]
    for profile in agent_profiles.list_profiles():
        data=profile.public()
        checks=preflight(profile) if not profile.errors else []
        data['ready']=not profile.errors and all(c['ok'] for c in checks)
        data['disabled_reasons']=list(profile.errors)+[c['message'] for c in checks if not c['ok']]
        profiles.append(data)
    return profiles


@router.get('/api/agents/{name}')
def get_agent(name: str):
    return _profile(name).public(detail=True)


@router.post('/api/agents/{name}/runs')
async def start_agent_run(name: str, req: StartAgentRun):
    from web_routes.runs import send_message
    profile = _profile(name)
    if profile.errors: raise HTTPException(409, list(profile.errors))
    from agent_preflight import preflight
    failures = [c['message'] for c in preflight(profile, fresh=True) if not c['ok']]
    if failures: raise HTTPException(409, failures)
    if not req.text.strip() and req.params is None: raise HTTPException(400, 'Provide text or parameters')
    if req.dry_run and not profile.dry_run: raise HTTPException(400, 'Dry run is not supported')
    values = None
    if req.params is not None:
        values, errors = agent_profiles.validate_params(profile, req.params)
        if errors: raise HTTPException(400, errors)
    if req.parent_session_id and web_app._store.load_session(req.parent_session_id) is None:
        raise HTTPException(404, 'Parent session not found')
    parent = web_app._store.load_session(req.parent_session_id) if req.parent_session_id else None
    cwd = req.working_directory or (web_helpers._workspace_root_for_session(parent) if parent else '') or profile.working_directory
    if cwd:
        cwd = web_app._resolve_path(cwd)
        if not utils.is_path_allowed(cwd) or not os.path.isdir(cwd): raise HTTPException(400, 'Working directory must be allowed and existing')
    with _creation_lock:
        web_app._store.ensure_agents_bucket(name)
        number = web_app._store.next_agent_run_number(name)
        meta = web_app._store.create_session(AGENTS_PROJECT_ID, name, f'Run {number}', 'native', kind='agent_run')
        meta.agent = name; meta.agent_version = profile.version; meta.agent_run_number = number
        meta.parent_session_id = req.parent_session_id
        meta.working_directory = cwd or web_app._agent_workspace_dir(meta.id)
        meta.run_settings = {'max_iterations': profile.max_iterations}
        if profile.approval != 'inherit': meta.run_settings['approval_mode'] = profile.approval
        if profile.reasoning_effort: meta.run_settings['reasoning_effort'] = profile.reasoning_effort
        web_app._store.update_session(meta)
    text = req.text.strip()
    if values is not None: text += '\nRun with these parameters:\n```json\n' + json.dumps(values, ensure_ascii=False) + '\n```'
    if req.dry_run: text += '\nDry run.'
    try:
        outcome = await send_message(meta.id, SendMessageRequest(text=text))
    except HTTPException:
        web_app._store.delete_session(AGENTS_PROJECT_ID, name, meta.id)
        raise
    if not isinstance(outcome, dict) or outcome.get('status') not in ('started', 'queued'):
        # A blocked start would leave an empty run behind and a card stuck on idle.
        web_app._store.delete_session(AGENTS_PROJECT_ID, name, meta.id)
        detail = outcome.get('detail') if isinstance(outcome, dict) else ''
        raise HTTPException(409, detail or 'The run could not be started')
    # Linked after the start so the parent's card first loads a running session.
    if req.parent_session_id:
        web_helpers._emit_session_event(req.parent_session_id, EventType.AGENT_RUN_LINK, {'run_session_id': meta.id, 'agent': name, 'run_number': number, 'request_text': req.text or json.dumps(values, ensure_ascii=False)})
    return web_app._store.load_session(meta.id).model_dump(mode='json')


class DraftRevision(BaseModel):
    feedback: str = Field(min_length=1, max_length=200000)


class DraftSave(BaseModel):
    replace: bool = False


class AgentFileSave(BaseModel):
    content: str = Field(max_length=2000000)
    base_hash: str | None = None


def _draft(draft_id):
    import agent_drafts
    try: return agent_drafts.read_draft(draft_id)
    except ValueError as exc: raise HTTPException(404, str(exc))


@router.get('/api/agent-drafts/{draft_id}')
def get_draft(draft_id: str):
    return _draft(draft_id)


@router.post('/api/agent-drafts/{draft_id}/revise')
async def revise_draft(draft_id: str, req: DraftRevision):
    import asyncio
    import agent_drafts
    previous = _draft(draft_id)
    try:
        draft = await asyncio.to_thread(agent_drafts.generate_draft, previous['intent'], previous['session_id'], previous, req.feedback)
    except Exception as exc: raise HTTPException(400, str(exc))
    web_helpers._emit_session_event(previous['session_id'], EventType.AGENT_DRAFT, draft)
    return draft


@router.post('/api/agent-drafts/{draft_id}/save')
def save_draft(draft_id: str, req: DraftSave):
    import agent_drafts
    _draft(draft_id)
    try: return agent_drafts.save_draft(draft_id, req.replace)
    except FileExistsError as exc: raise HTTPException(409, str(exc))
    except ValueError as exc: raise HTTPException(400, str(exc))


@router.delete('/api/agent-drafts/{draft_id}')
def discard_draft(draft_id: str):
    import agent_drafts
    import shutil
    _draft(draft_id)
    shutil.rmtree(agent_drafts.draft_path(draft_id))
    return {'status': 'discarded'}


@router.post('/api/agent-drafts/{draft_id}/dry-run')
def dry_run_draft(draft_id: str):
    import agent_drafts
    import shutil
    import tempfile
    from web_ui_adapter import WebUI
    from agent_runner import run_agent_script
    from web_runs import _finish_run_and_start_next
    draft = _draft(draft_id)
    if draft['errors'] or not draft['dry_run']: raise HTTPException(409, 'Draft is invalid or does not support dry run')
    sid = draft['session_id']
    with web_app._run_lock_for(sid):
        meta = web_app._store.load_session(sid)
        if not meta: raise HTTPException(404, 'Draft session no longer exists')
        if web_app._manager.get_active_run(sid): raise HTTPException(409, 'Wait for the current run to finish')
        run = web_app._manager.start_run(sid)
        meta.status = 'running'; web_app._store.update_session(meta)
        web_app._manager.notify_run_state(sid, 'running')
    def worker():
        ui = WebUI(sid, web_app._manager, run, web_app._store)
        try:
            # Always ask, including auto_approve sessions. The card contains all code.
            approved = ui.request_approval('draft_agent_dry_run', json.dumps({'agent': draft['name'], 'files': draft['files'], 'params': draft['example_params']}, ensure_ascii=False), None)
            output = 'Dry run denied.'
            if approved and not run.cancel_event.is_set():
                with tempfile.TemporaryDirectory(prefix='mh-draft-') as tmp:
                    folder = __import__('pathlib').Path(tmp)/draft['name']
                    shutil.copytree(agent_drafts.draft_path(draft_id)/draft['name'], folder)
                    profile = agent_profiles.read_profile(folder)
                    output = run_agent_script(profile, draft['example_params'], True, cwd=web_helpers._workspace_root_for_session(meta), run_id=sid, cancel_event=run.cancel_event, emit=lambda typ, data: None)
            draft['output'] = output
            (agent_drafts.draft_path(draft_id)/'draft.json').write_text(json.dumps(draft,ensure_ascii=False),encoding='utf-8')
            ui._emit(EventType.AGENT_DRAFT, draft)
        except Exception as exc: ui.show_error(str(exc))
        finally:
            ui.show_agent_finished('interrupted' if run.cancel_event.is_set() else 'completed')
            _finish_run_and_start_next(sid)
    run.thread = threading.Thread(target=worker, daemon=True); run.thread.start()
    return {'status':'started'}


def _agent_file(name, relative):
    import agent_drafts
    profile = _profile(name)
    try: target = (profile.path / agent_drafts.safe_relative(relative)).resolve()
    except ValueError as exc: raise HTTPException(400, str(exc))
    if not target.is_relative_to(profile.path) or not target.is_file(): raise HTTPException(404, 'Agent file not found')
    if target.stat().st_size > 2000000: raise HTTPException(400, 'File exceeds 2 MB')
    return target


@router.get('/api/agents/{name}/files/{path:path}')
def get_agent_file(name: str, path: str):
    from web_ui_adapter import content_hash
    target = _agent_file(name, path)
    try: content = target.read_text(encoding='utf-8')
    except UnicodeError: raise HTTPException(400, 'Only UTF-8 text files can be edited')
    return {'path':str(target),'content':content,'content_hash':content_hash(content)}


from fastapi import Request


@router.put('/api/agents/{name}/files/{path:path}')
def save_agent_file(name: str, path: str, req: AgentFileSave, request: Request):
    from web_ui_adapter import content_hash
    from web_desktop import _is_desktop_request
    if not _is_desktop_request(request): raise HTTPException(403, 'File editing is only available in the desktop app')
    with _creation_lock:
        target = _agent_file(name, path)
        old = target.read_text(encoding='utf-8')
        if not req.base_hash or content_hash(old) != req.base_hash: raise HTTPException(409, 'File changed on disk; reload before saving')
        # Atomic replacement preserves a complete manifest during catalog refresh.
        temporary = target.with_name(target.name + '.mh-save')
        temporary.write_text(req.content,encoding='utf-8'); temporary.replace(target)
    return {'path':str(target),'content_hash':content_hash(req.content)}


@router.get('/api/agents/{name}/preflight')
def agent_preflight(name: str):
    from agent_preflight import preflight
    return preflight(_profile(name))


class OptionsRequest(BaseModel):
    params: dict = Field(default_factory=dict)


@router.post('/api/agents/{name}/options/{param}')
def agent_param_options(name: str, param: str, req: OptionsRequest):
    """Choices for an `options_from: script` parameter, listed by the agent's script."""
    from agent_runner import list_param_options
    profile = _profile(name)
    if param not in profile.params or profile.params[param].get('options_from') != 'script':
        raise HTTPException(404, 'This parameter has no script-provided choices')
    if profile.errors: raise HTTPException(409, list(profile.errors))
    return list_param_options(profile, param, req.params)

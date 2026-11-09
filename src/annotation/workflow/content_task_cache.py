"""Durable, validated per-task results within a native run."""
import hashlib
import json
import re
import uuid
import zlib
from dataclasses import asdict
from pathlib import Path

from annotation.domain.artifacts import ContentTask, ContentArtifact, ContentUnitLoopTrace, ContextPack
from annotation.prompt_archive import read_artifact_json, write_artifact_json
from annotation.prompt_loader import load_prompt
from annotation.workflow.checkpoints import checkpoint_directory
from annotation.workflow.budget import PromptBudget
from annotation.workflow.content_support import _content_hard_check


def prompt_fingerprint(name):
    template = (Path(__file__).resolve().parents[1] / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    placeholders = dict.fromkeys(re.findall(r"\{\{(\w+)\}\}", template), "<runtime-data>")
    return load_prompt(name, **placeholders)


def location(batch, task):
    control = batch.ctx.run_control
    if control is None or task.knowledge_unit_id not in batch.units:
        return None
    source = [{k:getattr(b,k) for k in ('source_ref','text','text_hash','parser_version')}
              for b in batch.state.get('source_blocks',[])]
    provider = batch.ctx.provider
    material = {'version':1,'source':source,'unit':batch.units[task.knowledge_unit_id].model_dump(mode='json'),
        'task':task.model_dump(mode='json',exclude={'status'}),'profile':batch.state.get('prompt_profile'),
        'prompts':[prompt_fingerprint(name) for name in ('content_agent_step','critique_content_artifact')],
        'provider':{k:getattr(provider,k,None) for k in ('provider','model','base_url','config_version','thinking')},
        'budget':asdict(PromptBudget.for_provider(provider)), 'attempts':batch.ctx.content_max_attempts}
    fingerprint = hashlib.sha256(json.dumps(material,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return checkpoint_directory(task.run_id,control.root)/'content-tasks'/f'{fingerprint}.json'


def load(batch, task, path):
    try:
        data = read_artifact_json(path)
        if not isinstance(data, dict):return None
        if data.get('schema_version') != 'content-task-result-v1':return None
        result = data['result']
        if not isinstance(result, dict):return None
        saved_task = ContentTask.model_validate(result['task'])
        artifacts = [ContentArtifact.model_validate(a) for a in result['artifacts']]
        traces = [ContentUnitLoopTrace.model_validate(t) for t in result['traces']]
        packs = [ContextPack.model_validate(p) for p in result['updated_packs']]
        if saved_task.model_dump(exclude={'status'}) != task.model_dump(exclude={'status'}):return None
        if not traces or not artifacts or not packs:return None
        for trace in traces:
            if trace.final_status != 'accepted' or trace.task_id != task.task_id:return None
        if any(a.status != 'accepted' for a in artifacts):return None
        for artifact in artifacts:
            check = _content_hard_check(task=task,unit=batch.units[task.knowledge_unit_id],
                context_pack=packs[0],valid_source_refs=set(batch.state['source_refs']),candidate_artifact=artifact)
            if check.status != 'accepted':return None
        if not {t.final_content_artifact_id for t in traces}.issubset({a.artifact_id for a in artifacts}):return None
        for trace in traces:
            for attempt in trace.attempts:
                for name in ('generation_provider_metadata','critic_provider_metadata'):
                    old = getattr(attempt,name)
                    setattr(attempt,name,{**old,'usage':{},'duration_ms':0,'cache_hit':True})
        return {**result,'task':saved_task,'artifacts':artifacts,'traces':traces,'updated_packs':packs,
                'provider_metadata':{**result.get('provider_metadata',{}),'cache_hit':True,'usage':{},'duration_ms':0}}
    except (OSError,ValueError,KeyError,TypeError,EOFError,zlib.error):
        return None


def save(path, result):
    from annotation.workflow.persistence import _model_dump
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary = path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
    try:
        write_artifact_json(temporary,{'schema_version':'content-task-result-v1','result':_model_dump(result)})
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


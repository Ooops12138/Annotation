"""One structured author/tool loop without a separate permission-to-write call."""
from collections import Counter
from dataclasses import replace
import hashlib
import json

from annotation.ingestion.semantic_chunker import build_semantic_blocks
from annotation.prompt_loader import load_prompt
from annotation.providers import ProviderError, StructuredGenerationRequest
from annotation.workflow.budget import PromptBudget
from annotation.workflow.content import build_context_pack, render_context_pack, candidate_prompt_data
from annotation.workflow.content_support import _content_prompt_task, _content_prompt_unit
from annotation.workflow.models import ContentAgentStep
from annotation.token_counting import count_tokens


def author_with_tools(provider, state, service):
    pack = state['context_pack']
    blocks = state.get('semantic_blocks') or build_semantic_blocks(state.get('source_blocks', []))
    usage = Counter()
    duration = 0
    recent = []  # Old diagnostics remain in pack.retrieval_calls, never masquerade as current health.
    budget = PromptBudget.for_provider(provider)
    for round_number in range(3):
        candidate = state.get('candidate_artifact')
        feedback = {'candidate': candidate_prompt_data(candidate, pack),
                    'hard_check': state['hard_check'].model_dump(mode='json') if state.get('hard_check') else None,
                    'critique': state.get('critic_parsed_output'), 'error': state.get('generation_error')}
        prompt = load_prompt('content_agent_step',
            CAPABILITIES=json.dumps({'textbook_modes':getattr(service,'available_modes',[]),
                'web_enabled':getattr(service,'web_enabled',False), 'searches_remaining':2-round_number,
                'current_results':recent}, ensure_ascii=False),
            KNOWLEDGE_UNIT_CONTEXT=_content_prompt_unit(state['unit']),
            CONTENT_TASK=_content_prompt_task(state['task']),
            CONTEXT_PACK=render_context_pack(pack, aliases=True),
            REVISION_CONTEXT=json.dumps(feedback,ensure_ascii=False))
        try:
            budget.check(prompt,ContentAgentStep)
            response = provider.generate_structured(StructuredGenerationRequest(
                prompt=prompt,schema=ContentAgentStep,max_output_tokens=budget.output_tokens,
                context={'unit':state['unit'],'context_pack':pack,'source_blocks':blocks},
                metadata={'agent':'content_agent_step','prompt_version':'content_agent_step:v1',
                    'run_id':state['run_id'],'task_id':state['task'].task_id,
                    'attempt':int(state.get('attempt',0))+1,'round':round_number+1}))
        except ProviderError as exc:
            exc.context_pack = pack
            exc.prompt = prompt
            raise
        step = ContentAgentStep.model_validate(response.value.model_dump(mode='python'))
        usage.update(response.usage)
        duration += response.duration_ms
        if step.action == 'write':
            if not pack.excerpts:
                error = ProviderError('教材检索未返回可用证据，未接受正文。', category='no_evidence')
                error.context_pack = pack
                raise error
            return replace(response,value=step.draft,usage=dict(usage),duration_ms=duration),pack,step,prompt
        if round_number == 2:
            error = ProviderError('content agent retrieval budget exhausted',category='retrieval_budget')
            error.context_pack = pack
            raise error
        if service is None:
            raise ProviderError('Textbook retrieval service is unavailable', category='retrieval_budget')
        queries = [('textbook',q) for q in step.textbook_queries]
        queries += [('web',q) for q in step.web_queries]
        new = build_context_pack(state['task'],state['unit'],state['blueprint'],blocks,
            search_tool=service,retrieval_queries=queries,textbook_mode=step.textbook_mode)
        seen = {e.chunk_ref or e.source_ref for e in pack.excerpts}
        excerpts = list(pack.excerpts)
        used = sum(count_tokens(e.text) for e in excerpts)
        for excerpt in new.excerpts:
            key = excerpt.chunk_ref or excerpt.source_ref
            if key not in seen and used + count_tokens(excerpt.text) <= new.input_budget_tokens:
                excerpts.append(excerpt)
                used += count_tokens(excerpt.text)
                seen.add(key)
        refs = list(dict.fromkeys(r for e in excerpts for r in (e.source_refs or [e.source_ref])))
        calls = [c.model_copy(update={'call_id':f"call-{state['task'].task_id}-{len(pack.retrieval_calls)+i+1:03d}"})
                 for i,c in enumerate(new.retrieval_calls)]
        recent = [{'query':c.query,'status':c.status,'error_code':c.error_code,
                   'actual_mode':c.metadata.get('actual_mode'),'returned_count':len(c.returned_excerpts)} for c in calls]
        pack = new.model_copy(update={'excerpts':excerpts,'source_refs':refs,'selected_source_refs':refs,
            'retrieved_source_refs':refs,'retrieval_calls':[*pack.retrieval_calls,*calls],
            'estimated_input_tokens':used,
            'source_snapshot':hashlib.sha256(json.dumps([e.model_dump(mode='json') for e in excerpts],sort_keys=True).encode()).hexdigest()})

"""Selected stored/live evidence. Observation never runs a command or a Tool."""

import json
from llm.core.models import StepKind


async def process_data(runtime, session_id):
    if runtime.builtin_tools is None:
        raise ValueError('Builtin process observation is not available')
    return await runtime.sessions[session_id].project.components.aget(runtime.builtin_tools.name)


async def catalog(runtime, session_id):
    session = runtime.sessions[session_id]
    observations, process_error = {}, None
    try:
        data = await process_data(runtime, session_id)
        observations = await data.alist_processes()
    except (ValueError, FileNotFoundError) as error:
        # A disabled/missing output destination does not remove stored Run/Step
        # observations. Report this partial failure explicitly to the UI.
        process_error = str(error)
    return {'processes': {identifier: item.to_dict() for identifier, item in observations.items()},
            'runs': [run.id for run in await session.runs.alist()], 'process_error': process_error}


async def steps(runtime, session_id, run_id):
    run = await runtime.sessions[session_id].runs.aload(run_id)
    return [{'id': step.id, 'name': step.name, 'status': str(step.status), 'kind': str(step.kind)}
            for step in await run.steps.alist() if step.kind in (StepKind.TOOL, StepKind.SHELL)]


async def page(runtime, session_id, target, offsets, limit):
    if type(limit) is not int or limit <= 0:
        raise ValueError('Select an explicit positive character page size')
    if not isinstance(target, list) or not target:
        raise ValueError('Select one observed process or Run/Step')
    project_id = runtime.sessions[session_id].project.id
    if target[0] == 'process' and len(target) == 2:
        data = await process_data(runtime, session_id)
        value = await data.aread_process(target[1], stdout_offset=offsets['stdout'],
                                          stderr_offset=offsets['stderr'], max_chars=limit)
        # A Project-owned retained command may predate this Session. Do not
        # invent Session/Run provenance merely because it was selected here.
        return {'source': {'project_id': project_id, 'process_id': target[1]},
                'offset_unit': 'unicode_characters', 'page': value}
    if target[0] != 'step' or len(target) != 3:
        raise ValueError('Invalid observation target')
    if type(offsets.get('text')) is not int or offsets['text'] < 0:
        raise ValueError('Step text offset must be nonnegative')
    run = await runtime.sessions[session_id].runs.aload(target[1])
    step = await run.steps.aload(target[2])
    if step.kind not in (StepKind.TOOL, StepKind.SHELL):
        raise ValueError('Select an observed Tool or shell Step')
    output = step.output
    # Serialize the public value for inspection; Data/output references remain
    # references. No referenced files are fetched or commands re-executed here.
    text = json.dumps(output.data if output is not None else None, ensure_ascii=False)
    start = offsets['text']
    if start > len(text):
        raise ValueError('Step text offset is outside this observation')
    end = min(len(text), start + limit)
    return {'source': {'project_id': project_id, 'session_id': session_id,
                       'run_id': target[1], 'step_id': target[2]},
            'offset_unit': 'unicode_characters',
            'page': {'name': step.name, 'status': str(step.status), 'error': step.error,
                     'text': text[start:end], 'offset': start, 'next_offset': end,
                     'remaining': len(text) - end}}

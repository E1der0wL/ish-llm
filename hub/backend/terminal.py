"""Selected stored/live evidence. Observation never runs a command or a Tool."""

import json
from llm.core.models import StepKind
from llm.components.tools.builtin.process_values import ProcessOutput
from .terminal_values import TerminalCatalog, TerminalStep, TerminalKind, TerminalOffsets, TerminalPage, ProcessSource, StepSource, StepPage


UNAVAILABLE = 'Builtin process observation is not available'
INVALID_LIMIT = 'Select an explicit positive character page size'
MISSING_TARGET = 'Select one observed process or Run/Step'
INVALID_TARGET = 'Invalid observation target'
INVALID_STEP = 'Select an observed Tool or shell Step'
INVALID_OFFSET = 'Step text offset is outside this observation'
PROCESS_TARGET_SIZE = 2
STEP_TARGET_SIZE = 3


async def process_data(runtime, session_id):
    if runtime.builtin_tools is None:
        raise ValueError(UNAVAILABLE)
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
    return TerminalCatalog(observations, tuple(run.id for run in await session.runs.alist()), process_error).to_dict()


async def steps(runtime, session_id, run_id):
    run = await runtime.sessions[session_id].runs.aload(run_id)
    return [TerminalStep(step.id, step.name, step.status, step.kind).to_dict()
            for step in await run.steps.alist() if step.kind in (StepKind.TOOL, StepKind.SHELL)]


async def page(runtime, session_id, target, offsets, limit):
    if type(limit) is not int or limit <= 0:
        raise ValueError(INVALID_LIMIT)
    if not isinstance(target, list) or not target:
        raise ValueError(MISSING_TARGET)
    project_id = runtime.sessions[session_id].project.id
    offsets = TerminalOffsets.from_dict(offsets)
    if target[0] == TerminalKind.PROCESS and len(target) == PROCESS_TARGET_SIZE:
        data = await process_data(runtime, session_id)
        value = await data.aread_process(target[1], stdout_offset=offsets.stdout,
                                          stderr_offset=offsets.stderr, max_chars=limit)
        # A Project-owned retained command may predate this Session. Do not
        # invent Session/Run provenance merely because it was selected here.
        return TerminalPage(ProcessSource(project_id, target[1]), ProcessOutput(**value)).to_dict()
    if target[0] != TerminalKind.STEP or len(target) != STEP_TARGET_SIZE:
        raise ValueError(INVALID_TARGET)
    run = await runtime.sessions[session_id].runs.aload(target[1])
    step = await run.steps.aload(target[2])
    if step.kind not in (StepKind.TOOL, StepKind.SHELL):
        raise ValueError(INVALID_STEP)
    output = step.output
    # Serialize the public value for inspection; Data/output references remain
    # references. No referenced files are fetched or commands re-executed here.
    text = json.dumps(output.data if output is not None else None, ensure_ascii=False)
    start = offsets.text
    if start > len(text):
        raise ValueError(INVALID_OFFSET)
    end = min(len(text), start + limit)
    return TerminalPage(StepSource(project_id, session_id, target[1], target[2]),
        StepPage(step.name, step.status, step.error, text[start:end], start, end, len(text) - end)).to_dict()

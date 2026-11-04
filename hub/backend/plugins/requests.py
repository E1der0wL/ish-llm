"""Explicit plugin request identities; execution/approval authority stays in llm."""

from uuid import uuid4

from llm.core.interactions import InteractionRequest, InteractionResponse
from llm.core.models import MessageRole
from .projects import PluginProjects
from .definitions import PluginContext
from .stream import PluginStream


class PluginRequests:
    """One shared-backend integration lifetime, never a second execution manager."""

    def __init__(self, backend):
        self.projects = PluginProjects(backend)
        self.generation = uuid4().hex
        self.streams = {}

    def close_observers(self):
        for stream in self.streams.values():
            stream.close()

    def observe(self, run):
        stream = self.streams.get(run.session_id)
        if stream is None:
            return False
        stream.signal()
        return True

    async def _session(self, plugin_id, session_id):
        session = await self.projects.session(plugin_id, session_id)
        self.streams.setdefault(session.id, PluginStream())
        return session

    async def _request(self, plugin_id, reference):
        if reference["generation"] != self.generation:
            raise RuntimeError("Hub restarted; reconnect using the stored Session and request IDs")
        session = await self._session(plugin_id, reference["session_id"])
        request = await session.run.arequest(reference["request_id"])
        await request.aget_data()  # Verify the persisted request, not just an ID shape.
        return request

    def _reference(self, session, request):
        return {"generation": self.generation, "project_id": session.project.id,
                "session_id": session.id, "request_id": request.id}

    async def register(self, plugin_id, definition):
        return await self.projects.register(plugin_id, definition)

    async def open_session(self, plugin_id, key, agent_ids):
        result = await self.projects.open_session(plugin_id, key, agent_ids)
        self.streams.setdefault(result["session_id"], PluginStream())
        return result

    async def submit(self, plugin_id, session_id, text, recipients, context, request_key):
        session = await self._session(plugin_id, session_id)
        prompt, options = (context or PluginContext()).prepare(text)
        # Plugin templates own their prompts/tools. Do not apply Home's intent
        # classification, completion gates or implicit recipient selection here.
        request = await session.run.submit(prompt, recipients=list(recipients),
                                          request_context=options, request_key=request_key)
        self.streams[session.id].signal()
        return self._reference(session, request)

    async def reconnect(self, plugin_id, session_id, request_id):
        session = await self._session(plugin_id, session_id)
        request = await session.run.arequest(request_id)
        record = await request.aget_data()
        if record.role != MessageRole.USER:
            raise ValueError("Expected a user request")
        return self._reference(session, request)

    async def poll(self, plugin_id, reference, cursors, revision, include_internal):
        request = await self._request(plugin_id, reference)
        result = await self.streams[request.session.id].read(request, cursors, revision,
                                                            include_internal=include_internal)
        return result

    async def questions(self, plugin_id, reference, run_id):
        run = await self._run(plugin_id, reference, run_id)
        return [view.to_dict() for view in await run.aquestions()]

    async def answer_question(self, plugin_id, reference, run_id, response):
        run = await self._run(plugin_id, reference, run_id)
        return (await run.aanswer_question(InteractionResponse.from_dict(response))).to_dict()

    async def cancel_question(self, plugin_id, reference, run_id, request):
        run = await self._run(plugin_id, reference, run_id)
        await run.acancel_question(InteractionRequest.from_dict(request))

    async def deliver_answer(self, plugin_id, reference, run_id, request_id, options):
        from ..interactions.questions import QuestionAnswers
        run = await self._run(plugin_id, reference, run_id)
        # A plugin may only steer a Run in its owned request; submitting still
        # goes through that same plugin-owned Session and native recipient checks.
        if options.get('mode') == 'steer':
            await self._run(plugin_id, reference, options['target_run_id'])
            options = {**options, 'run_id': options['target_run_id']}
            del options['target_run_id']
        return (await QuestionAnswers.deliver(run, request_id, **options)).to_dict()

    async def get_delivery(self, plugin_id, reference, run_id, request_id):
        run = await self._run(plugin_id, reference, run_id)
        receipt = await run.get_delivery(request_id)
        return receipt.to_dict() if receipt else None

    async def question_targets(self, plugin_id, reference, run_id):
        run = await self._run(plugin_id, reference, run_id)
        return [target.to_dict() for target in await run.ainstruction_targets()]

    async def _run(self, plugin_id, reference, run_id):
        request = await self._request(plugin_id, reference)
        for run in await request.aget_runs():
            if run.id == run_id:
                return run
        raise ValueError("Run does not belong to this request")

    async def cancel(self, plugin_id, reference):
        request = await self._request(plugin_id, reference)
        return await request.cancel()

    async def interrupt(self, plugin_id, reference, run_id):
        run = await self._run(plugin_id, reference, run_id)
        return await run.session.run.interrupt(run.id)

    async def steer(self, plugin_id, reference, run_id, text, request_key):
        run = await self._run(plugin_id, reference, run_id)
        receipt = await run.session.run.steer(run.id, text, request_key=request_key)
        return receipt.to_dict()

    async def respond(self, plugin_id, reference, run_id, response, request_key):
        run = await self._run(plugin_id, reference, run_id)
        stored = await run.arespond(InteractionResponse.from_dict(response), request_key=request_key)
        return stored.to_dict()  # Deliberately does NOT resume execution.

    async def cancel_interaction(self, plugin_id, reference, run_id, interaction):
        run = await self._run(plugin_id, reference, run_id)
        result = await run.acancel_interaction(InteractionRequest.from_dict(interaction))
        return result.to_dict()

    async def resume_plan(self, plugin_id, reference, run_id):
        run = await self._run(plugin_id, reference, run_id)
        record = await run.aget_data()
        return (await run.session.run.resume_plan(run.id, agent_id=record.agent_id)).to_dict()

    async def resume(self, plugin_id, reference, run_id, options):
        run = await self._run(plugin_id, reference, run_id)
        record = await run.aget_data()
        # Backend validates checkpoint evidence and uncertain-effect decisions.
        # A failed plan is never converted into an automatic restart/replay.
        allowed = {"retry_nodes", "decisions", "partial_output", "expected_revision"}
        if options.keys() - allowed:
            raise ValueError("Unsupported resume option")
        request = await run.session.run.resume(run.id, agent_id=record.agent_id, **options)
        self.streams[run.session.id].signal()
        return self._reference(run.session, request)

    async def receipt(self, plugin_id, session_id, request_key):
        session = await self._session(plugin_id, session_id)
        result = await session.run.get_receipt(request_key)
        return result.to_dict() if result is not None else None

    async def catalog(self):
        return await self.projects.catalog()

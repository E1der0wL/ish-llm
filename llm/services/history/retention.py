"""History eligibility and explicit deletion; Application owns retention selection."""

from datetime import datetime, timezone
import json
from llm.core.contracts import ResourceRef
from llm.core.plans import RetentionPlan, RetentionUnit
from llm.core.models import MessageStatus, now, new_id, RunStatus, ProjectConfig
from llm.services.infrastructure.storage import revision_token, atomic_json, read_json, record, reject_links, tree_snapshot, child
from llm.services.runtime.usage import project_usage_policies, protects_project_usage


class HistoryRetention:
    def __init__(self, manager, counter=None, *, counter_params=None):
        self.manager, self.counter = manager, counter
        self.counter_params = {} if counter_params is None else counter_params
        if not isinstance(self.counter_params, dict) or "messages" in self.counter_params:
            raise ValueError("Retention counter parameters cannot override messages")
        ProjectConfig.validate_json(self.counter_params)
        if counter is not None and not callable(counter):
            raise ValueError("Retention requires an explicitly selected callable counter")
        if self.counter_params and counter is None:
            raise ValueError("Retention counter parameters require a selected counter")

    @staticmethod
    def _stamps(root):
        excluded = {str(path.relative_to(root)) for path in root.rglob("*")
                    if "logs" in path.relative_to(root).parts}
        return tree_snapshot(root, exclude=excluded)

    @staticmethod
    def _result(project, unit, candidates, protected, sources, size, tokens):
        # Token estimates select Application targets, never grant deletion authority.
        proof = {"unit": unit, "sources": sources, "candidates": [], "protected": []}
        for destination, rows in ((proof["candidates"], candidates), (proof["protected"], protected)):
            for row in rows:
                evidence = dict(row)
                evidence.pop("tokens")
                destination.append(evidence)
        return RetentionPlan(ResourceRef("project", project.id, project_id=project.id),
            unit, candidates, protected, size, tokens, revision_token(proof), unit == RetentionUnit.RUN)

    def _references(self, project):
        return self.manager.components.history_references(project)

    def _history(self, session):
        runs = self.manager.sessions.run_repository.list(session)
        messages = self.manager.sessions.conversations(session).list()
        if (not self.manager.ownership.session_attached((session.project_id, session.id))
                and not any(run.status in (RunStatus.PENDING, RunStatus.RUNNING) for run in runs)):
            _, messages, runs = self.manager.sessions.validate_history(session)
        return runs, messages

    def _run_plan(self, project):
        refs = self._references(project)
        usage_policies = project_usage_policies(project, self.manager.sessions)
        rows, protected, sources = [], [], [record(project)]
        clock = datetime.now(timezone.utc)
        total_bytes = total_tokens = 0
        for session in self.manager.sessions.list(project, include_deleted=True):
            runs, messages = self._history(session)
            sources.extend([record(session), *[record(m) for m in messages]])
            reasons = []
            if self.manager.ownership.session_attached((project.id, session.id)) or session.current_run_id:
                reasons.append("runtime_attached")
            if project.conversation_storage != "file":
                reasons.append("non_file_conversation")
            if any(m.status in (MessageStatus.QUEUED, MessageStatus.STREAMING) for m in messages):
                reasons.append("pending_message")
            if session.metadata.get("retain") or session.id in refs["session_ids"]:
                reasons.append("pinned_or_component_reference")
            if any((session.paths.state / "maintenance").glob("*.json")):
                reasons.append("pending_maintenance")
            # 외부 효과 원장은 Run 제거와 독립적으로 수명을 정해야 하므로 보수적으로 유지한다.
            if self.manager.sessions.run_repository.has_tool_operations(session):
                reasons.append("external_operation_history")
            keep = set(refs["run_ids"])
            protected_messages = set(refs["message_ids"])
            for run in runs:
                sources.append(record(run))
                source = run.metadata.get("resume", {}).get("run_id")
                if source:
                    keep.add(source)
                if run.status != RunStatus.COMPLETED:
                    for name in run.metadata.get("checkpoints", []):
                        checkpoint = self.manager.sessions.run_repository.checkpoint(run, name)
                        sources.append(checkpoint)
                        protected_messages.update(checkpoint["header"].get("message_ids", []))
                        if "steering" in run.metadata:
                            from llm.services.runtime.steering import instruction_records
                            protected_messages.update(mid for _, value in instruction_records(checkpoint) for mid in value["message_ids"])
            keep.update(m.run_id for m in messages if m.id in protected_messages and m.run_id)
            for run in runs:
                stamps = self._stamps(run.paths.root)
                owned = [m for m in messages if m.run_id == run.id or m.id in (run.input_message_id, run.assistant_message_id)]
                size = sum(s["bytes"] for s in stamps.values()) + sum(len(json.dumps(record(m), ensure_ascii=False).encode("utf-8")) for m in owned)
                tokens = 0
                if self.counter is not None:
                    tokens = self.counter({**self.counter_params,
                        "messages": [{"role": m.role.value, "content": m.content} for m in owned]})
                    if type(tokens) is not int or tokens < 0:
                        raise ValueError("Retention counter requires nonnegative integer tokens")
                total_bytes += size
                total_tokens += tokens
                row = {"session_id": session.id, "run_id": run.id, "status": run.status,
                       "message_ids": [m.id for m in owned],
                       "message_revisions": {m.id: revision_token(record(m)) for m in owned},
                       "bytes": size, "tokens": tokens, "last_activity": run.ended_at or run.created_at,
                       "revision": revision_token(stamps), "record_revision": revision_token(record(run))}
                blocked = list(reasons)
                if run.status != RunStatus.COMPLETED:
                    blocked.append("unfinished_or_resumable_run")
                if run.id in keep:
                    blocked.append("recent_or_referenced")
                if protects_project_usage(run.metadata.get("completions", []), usage_policies, at=clock):
                    blocked.append("active_usage_accounting")
                (protected if blocked else rows).append({**row, **({"reasons": blocked} if blocked else {})})
        return self._result(project, RetentionUnit.RUN, rows, protected, sources, total_bytes, total_tokens)

    def _finish(self, session, path):
        intent = read_json(reject_links(path))
        if intent.get("session_id") != session.id or intent.get("kind") != "run_retention":
            raise ValueError("Invalid retention journal")
        if self.manager.ownership.session_attached((session.project_id, session.id)):
            raise ValueError("Shut down Session runtime before retention recovery")
        repository = self.manager.sessions.run_repository
        # rmtree 도중 종료되어 run.json만 사라진 경우도, 승인된 삭제 저널을 통해 마무리한다.
        repository.recover_history_deletions(session, [r["run_id"] for r in intent["runs"]])
        for row in intent["runs"]:
            try:
                run = repository.load(session, row["run_id"])
            except FileNotFoundError:
                continue
            if run.status != RunStatus.COMPLETED or revision_token(record(run)) != row["record_revision"]:
                raise ValueError("Retention source changed; manual review is required")
        # 의도를 먼저 저장한 뒤 대화를 정리한다. 중단 시 runtime 재접속을 차단한다.
        store = self.manager.sessions.conversations(session)
        versions = {mid: version for row in intent["runs"] for mid, version in row["message_revisions"].items()}
        for message in store.list():
            if message.id in versions and revision_token(record(message)) != versions[message.id]:
                raise ValueError("Retention conversation changed; manual review is required")
        store.prune(set(versions))
        for row in intent["runs"]:
            repository.delete_history(session, row["run_id"])
        from llm.services.infrastructure.storage import child
        receipt = child(session.paths.state / "retention_history", intent["id"]).with_suffix(".json")
        atomic_json(reject_links(receipt), {**intent, "completed_at": now()})
        from llm.services.infrastructure.storage import unlink_file
        unlink_file(path)
        return intent["id"]

    def plan(self, project, *, unit) -> RetentionPlan:
        unit = RetentionUnit(unit)
        if unit == RetentionUnit.RUN:
            return self._run_plan(project)
        refs = self._references(project)
        usage_policies = project_usage_policies(project, self.manager.sessions)
        items, protected, sources = [], [], [record(project)]
        total_bytes = total_tokens = 0
        now = datetime.now(timezone.utc)
        for session in self.manager.sessions.list(project, include_deleted=True):
            runs, messages = self._history(session)
            sources.extend([record(session), *[record(run) for run in runs], *[record(m) for m in messages]])
            stamps = self._stamps(session.paths.root)
            size = sum(stamp["bytes"] for stamp in stamps.values())
            tokens = 0
            if self.counter is not None:
                tokens = self.counter({**self.counter_params,
                    "messages": [{"role": m.role.value, "content": m.content} for m in messages]})
                if type(tokens) is not int or tokens < 0:
                    raise ValueError("Retention counter requires nonnegative integer tokens")
            total_bytes += size
            total_tokens += tokens
            reasons = []
            if session.id in refs["session_ids"] or any(r.id in refs["run_ids"] for r in runs) or any(m.id in refs["message_ids"] for m in messages):
                reasons.append("component_reference")
            if any((session.paths.state / "maintenance").glob("*.json")):
                reasons.append("pending_maintenance")
            if self.manager.ownership.session_attached((project.id, session.id)):
                reasons.append("runtime_attached")
            if session.current_run_id or any(r.status != RunStatus.COMPLETED for r in runs):
                reasons.append("unfinished_or_resumable_run")
            completed = {r.id for r in runs if r.status == RunStatus.COMPLETED}
            if any(m.status in (MessageStatus.QUEUED, MessageStatus.STREAMING) or m.status == MessageStatus.COMMITTED and m.run_id not in completed for m in messages):
                reasons.append("pending_message")
            if self.manager.sessions.run_repository.has_tool_operations(session):
                reasons.append("external_operation_history")
            if session.metadata.get("retain"):
                reasons.append("pinned")
            if project.conversation_storage != "file":
                reasons.append("non_file_conversation")
            if protects_project_usage([e for r in runs for e in r.metadata.get("completions", [])],
                                      usage_policies, at=now):
                reasons.append("active_usage_accounting")
            end = max([r.ended_at or r.created_at for r in runs] + [session.created_at])
            row = {"session_id": session.id, "bytes": size, "tokens": tokens, "last_activity": end,
                   "revision": revision_token(stamps)}
            if reasons:
                protected.append({**row, "reasons": reasons})
            else:
                items.append(row)
        return self._result(project, RetentionUnit.SESSION, items, protected, sources, total_bytes, total_tokens)

    def apply(self, project, *, unit, targets, expected_version):
        unit = RetentionUnit(unit)
        if not isinstance(targets, (list, tuple)):
            raise ValueError("History pruning requires explicit targets")
        fields = {"session_id", "run_id"} if unit == RetentionUnit.RUN else {"session_id"}
        keys = []
        for target in targets:
            if not isinstance(target, dict) or target.keys() != fields:
                raise ValueError("History target fields do not match the explicit unit")
            if any(not isinstance(target[name], str) for name in fields):
                raise ValueError("History targets require owned identifiers")
            session_path = child(project.paths.sessions, target["session_id"])
            if unit == RetentionUnit.RUN:
                child(session_path / "runs", target["run_id"])
            keys.append(tuple(target[name] for name in sorted(fields)))
        if len(keys) != len(set(keys)):
            raise ValueError("History targets must be unique")
        if not isinstance(expected_version, str) or not expected_version:
            raise ValueError("History pruning requires a reviewed plan version")
        plan = self.plan(project, unit=unit)
        if not expected_version or expected_version != plan.version:
            raise ValueError("retention_conflict: review a fresh retention plan")
        eligible = {tuple(row[name] for name in sorted(fields)): row for row in plan.candidates}
        if any(key not in eligible for key in keys):
            raise ValueError("History pruning cannot delete protected or unknown records")
        selected = [eligible[key] for key in keys]
        if unit == RetentionUnit.RUN:
            grouped = {}
            for row in selected:
                grouped.setdefault(row["session_id"], []).append(row)
            for identifier, rows in grouped.items():
                session = self.manager.sessions.load(project, identifier)
                self.manager.sessions.require_inactive(session)
                path = reject_links(session.paths.state / "maintenance" / "retention.json")
                atomic_json(path, {"id": new_id(), "kind": "run_retention", "session_id": session.id,
                                   "created_at": now(), "runs": rows})
                self._finish(session, path)
            return selected
        for row in selected:
            session = self.manager.sessions.load(project, row["session_id"])
            self.manager.sessions.delete(session, permanent=True)
        return selected

    def recover(self, project):
        """이미 승인·기록된 삭제 의도만 완료한다. 새 보관 후보는 선택하지 않는다."""
        completed = []
        for session in self.manager.sessions.list(project, include_deleted=True):
            path = session.paths.state / "maintenance" / "retention.json"
            if path.exists():
                completed.append(self._finish(session, path))
        return completed

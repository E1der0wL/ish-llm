"""프로젝트 보관 정책의 미리보기와 명시적 정리. 재개·외부 효과 기록은 자동 삭제하지 않는다."""

from datetime import datetime, timezone
import json
from llm.core.contracts import ResourceRef
from llm.core.plans import RetentionPlan
from llm.core.models import now, new_id
from llm.services.infrastructure.storage import revision_token, atomic_json, read_json, record, sync_directory, reject_links


class HistoryRetention:
    def __init__(self, manager, counter=None):
        self.manager, self.counter = manager, counter

    def _references(self, project):
        refs = {"session_ids": set(), "run_ids": set(), "message_ids": set()}
        for name in self.manager.components.names():
            component = self.manager.components.get(name)
            reader = getattr(component, "history_references", None)
            if reader and component.root(project).exists():
                for kind, values in reader(project).items():
                    if kind in refs:
                        refs[kind].update(values)
        return refs

    def _run_plan(self, project):
        policy, refs = project.config.policies.get("retention", {}), self._references(project)
        rows, protected, sources = [], [], []
        clock = datetime.now(timezone.utc)
        total_bytes = total_tokens = 0
        for session in self.manager.sessions.list(project, include_deleted=True):
            runs = self.manager.sessions.run_repository.list(session)
            messages = self.manager.sessions.conversations(session).list()
            sources.extend([record(session), *[record(m) for m in messages]])
            reasons = []
            if self.manager.ownership.session_attached((project.id, session.id)) or session.current_run_id:
                reasons.append("runtime_attached")
            if project.conversation_storage != "file":
                reasons.append("non_file_conversation")
            if any(m.status in ("queued", "streaming") for m in messages):
                reasons.append("pending_message")
            if session.metadata.get("retain") or session.id in refs["session_ids"]:
                reasons.append("pinned_or_component_reference")
            if any((session.paths.state / "maintenance").glob("*.json")):
                reasons.append("pending_maintenance")
            # 외부 효과 원장은 Run 제거와 독립적으로 수명을 정해야 하므로 보수적으로 유지한다.
            if (session.paths.state / "tool_operations").exists():
                reasons.append("external_operation_history")
            keep = set(refs["run_ids"])
            protected_messages = set(refs["message_ids"])
            completed = sorted((r for r in runs if r.status == "completed"),
                               key=lambda r: (r.ended_at or r.created_at, r.id), reverse=True)
            keep.update(r.id for r in completed[:policy.get("keep_runs", 0)])
            for run in runs:
                sources.append(record(run))
                source = run.metadata.get("resume", {}).get("run_id")
                if source:
                    keep.add(source)
                if run.status != "completed":
                    for name in run.metadata.get("checkpoints", []):
                        checkpoint = self.manager.sessions.run_repository.checkpoint(run, name)
                        sources.append(checkpoint)
                        protected_messages.update(checkpoint["header"].get("message_ids", []))
                        if "steering" in run.metadata:
                            from llm.services.runtime.steering import instruction_records
                            protected_messages.update(mid for _, value in instruction_records(checkpoint) for mid in value["message_ids"])
            keep.update(m.run_id for m in messages if m.id in protected_messages and m.run_id)
            for run in runs:
                stamps = {}
                for path in run.paths.root.rglob("*"):
                    if path.is_symlink():
                        raise ValueError("Retention cannot inspect linked files")
                    relative = path.relative_to(run.paths.root)
                    if path.is_file() and "logs" not in relative.parts:
                        stat = path.stat()
                        stamps[str(relative)] = [stat.st_ino, stat.st_size, stat.st_mtime_ns]
                owned = [m for m in messages if m.run_id == run.id or m.id in (run.input_message_id, run.assistant_message_id)]
                size = sum(s[1] for s in stamps.values()) + sum(len(json.dumps(record(m), ensure_ascii=False).encode("utf-8")) for m in owned)
                tokens = 0
                if policy.get("max_tokens") is not None:
                    if self.counter is None:
                        raise ValueError("Retention requires a registered token counter")
                    tokens = self.counter({**policy.get("counter_params", {}),
                        "messages": [{"role": m.role.value, "content": m.content} for m in owned]})
                    if type(tokens) is not int or tokens < 0:
                        raise ValueError("Retention counter requires nonnegative integer tokens")
                total_bytes += size
                total_tokens += tokens
                row = {"session_id": session.id, "run_id": run.id, "message_ids": [m.id for m in owned],
                       "message_revisions": {m.id: revision_token(record(m)) for m in owned},
                       "bytes": size, "tokens": tokens, "last_activity": run.ended_at or run.created_at,
                       "revision": revision_token(stamps), "record_revision": revision_token(record(run))}
                blocked = list(reasons)
                if run.status != "completed":
                    blocked.append("unfinished_or_resumable_run")
                if run.id in keep:
                    blocked.append("recent_or_referenced")
                usage = project.config.policies.get("usage", {})
                if usage.get("project_max_calls") is not None or usage.get("project_max_tokens") is not None:
                    if any(usage.get("period_seconds") is None or (clock - datetime.fromisoformat(e["started_at"])).total_seconds() <= usage.get("period_seconds")
                           for e in run.metadata.get("completions", [])):
                        blocked.append("active_usage_accounting")
                (protected if blocked else rows).append({**row, **({"reasons": blocked} if blocked else {})})
        left_bytes, left_tokens, selected = total_bytes, total_tokens, []
        for row in sorted(rows, key=lambda r: (r["last_activity"], r["session_id"], r["run_id"])):
            expired = policy.get("max_age_seconds") is not None and (clock - datetime.fromisoformat(row["last_activity"])).total_seconds() > policy.get("max_age_seconds")
            if expired or policy.get("max_bytes") is not None and left_bytes > policy.get("max_bytes") or policy.get("max_tokens") is not None and left_tokens > policy.get("max_tokens"):
                selected.append(row)
                left_bytes -= row["bytes"]
                left_tokens -= row["tokens"]
        result = {"policy": policy, "candidates": selected, "protected": protected, "estimated": True,
                  "bytes_before": total_bytes, "bytes_after": left_bytes, "tokens_before": total_tokens, "tokens_after": left_tokens}
        return RetentionPlan(ResourceRef("project", project.id, project_id=project.id),
                             **result, version=revision_token({**result, "sources": sources}))

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
            if run.status != "completed" or revision_token(record(run)) != row["record_revision"]:
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

    def plan(self, project) -> RetentionPlan:
        settings = project.config.policies.get("retention", {})
        if any(settings.get(k) is not None for k in ("max_age_seconds", "max_bytes", "max_tokens")) and "unit" not in settings:
            raise ValueError("Retention requires an explicit unit")
        if settings.get("unit") == "run":
            return self._run_plan(project)
        policy = project.config.policies.get("retention", {})
        refs = self._references(project)
        items, protected = [], []
        total_bytes = total_tokens = 0
        now = datetime.now(timezone.utc)
        for session in self.manager.sessions.list(project, include_deleted=True):
            runs = self.manager.sessions.run_repository.list(session)
            paths = list(session.paths.root.rglob("*"))
            if any(p.is_symlink() for p in paths):
                raise ValueError("Retention cannot inspect linked files")
            # 조회 자체가 운영 로그를 쓰므로 정책 대상은 도메인 기록/산출물이다. 로그 회전은 logger 책임이다.
            stamps = {}
            for path in paths:
                relative = path.relative_to(session.paths.root)
                if path.is_file() and "logs" not in relative.parts:
                    stat = path.stat()
                    stamps[str(relative)] = [stat.st_ino, stat.st_size, stat.st_mtime_ns]
            size = sum(stamp[1] for stamp in stamps.values())
            messages = self.manager.sessions.conversations(session).list()
            tokens = 0
            if policy.get("max_tokens") is not None:
                if self.counter is None:
                    raise ValueError("Retention requires a registered token counter")
                tokens = self.counter({**policy.get("counter_params", {}),
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
            if session.current_run_id or any(r.status != "completed" for r in runs):
                reasons.append("unfinished_or_resumable_run")
            completed = {r.id for r in runs if r.status == "completed"}
            if any(m.status in ("queued", "streaming") or m.status == "committed" and m.run_id not in completed for m in messages):
                reasons.append("pending_message")
            if (session.paths.state / "tool_operations").exists():
                reasons.append("external_operation_history")
            if session.metadata.get("retain"):
                reasons.append("pinned")
            if project.conversation_storage != "file":
                reasons.append("non_file_conversation")
            usage = project.config.policies.get("usage", {})
            if usage.get("project_max_calls") is not None or usage.get("project_max_tokens") is not None:
                period = usage.get("period_seconds")
                if any(period is None or (now - datetime.fromisoformat(e["started_at"])).total_seconds() <= period
                       for r in runs for e in r.metadata.get("completions", [])):
                    reasons.append("active_usage_accounting")
            end = max([r.ended_at or r.created_at for r in runs] + [session.created_at])
            row = {"session_id": session.id, "bytes": size, "tokens": tokens, "last_activity": end,
                   "revision": revision_token(stamps)}
            if reasons:
                protected.append({**row, "reasons": reasons})
            else:
                items.append(row)
        remaining_bytes, remaining_tokens, selected = total_bytes, total_tokens, []
        for row in sorted(items, key=lambda r: (r["last_activity"], r["session_id"])):
            expired = policy.get("max_age_seconds") is not None and (now - datetime.fromisoformat(row["last_activity"])).total_seconds() > policy.get("max_age_seconds")
            oversize = policy.get("max_bytes") is not None and remaining_bytes > policy.get("max_bytes")
            overtokens = policy.get("max_tokens") is not None and remaining_tokens > policy.get("max_tokens")
            if expired or oversize or overtokens:
                selected.append(row)
                remaining_bytes -= row["bytes"]
                remaining_tokens -= row["tokens"]
        result = {"policy": policy, "candidates": selected, "protected": protected,
                  "bytes_before": total_bytes, "bytes_after": remaining_bytes,
                  "tokens_before": total_tokens, "tokens_after": remaining_tokens}
        return RetentionPlan(ResourceRef("project", project.id, project_id=project.id),
                             **result, version=revision_token(result))

    def apply(self, project, expected_version):
        plan = self.plan(project)
        if not expected_version or expected_version != plan.version:
            raise ValueError("retention_conflict: review a fresh retention plan")
        if plan.policy.get("unit") == "run":
            grouped = {}
            for row in plan.candidates:
                grouped.setdefault(row["session_id"], []).append(row)
            for identifier, rows in grouped.items():
                session = self.manager.sessions.load(project, identifier)
                self.manager.sessions.require_inactive(session)
                path = reject_links(session.paths.state / "maintenance" / "retention.json")
                atomic_json(path, {"id": new_id(), "kind": "run_retention", "session_id": session.id,
                                   "created_at": now(), "runs": rows})
                self._finish(session, path)
            return plan
        for row in plan.candidates:
            session = self.manager.sessions.load(project, row["session_id"])
            self.manager.sessions.delete(session, permanent=True)
        return plan

    def recover(self, project):
        """이미 승인·기록된 삭제 의도만 완료한다. 새 보관 후보는 선택하지 않는다."""
        completed = []
        for session in self.manager.sessions.list(project, include_deleted=True):
            path = session.paths.state / "maintenance" / "retention.json"
            if path.exists():
                completed.append(self._finish(session, path))
        return completed

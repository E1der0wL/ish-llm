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
        refs = {"task_ids": set(), "run_ids": set(), "message_ids": set()}
        for name in self.manager.components.names():
            component = self.manager.components.get(name)
            reader = getattr(component, "history_references", None)
            if reader and component.root(project).exists():
                for kind, values in reader(project).items():
                    if kind in refs:
                        refs[kind].update(values)
        return refs

    def _run_plan(self, project):
        policy, refs = project.config.policies["retention"], self._references(project)
        rows, protected, sources = [], [], []
        clock = datetime.now(timezone.utc)
        total_bytes = total_tokens = 0
        for task in self.manager.tasks.list(project, include_deleted=True):
            runs = self.manager.tasks.run_repository.list(task)
            messages = self.manager.tasks.conversations(task).list()
            sources.extend([record(task), *[record(m) for m in messages]])
            reasons = []
            if self.manager.ownership.task_attached((project.id, task.id)) or task.current_run_id:
                reasons.append("runtime_attached")
            if project.conversation_storage != "file":
                reasons.append("non_file_conversation")
            if any(m.status in ("queued", "streaming") for m in messages):
                reasons.append("pending_message")
            if task.metadata.get("retain") or task.id in refs["task_ids"]:
                reasons.append("pinned_or_component_reference")
            if any((task.paths.state / "maintenance").glob("*.json")):
                reasons.append("pending_maintenance")
            # 외부 효과 원장은 Run 제거와 독립적으로 수명을 정해야 하므로 보수적으로 유지한다.
            if (task.paths.state / "tool_operations").exists():
                reasons.append("external_operation_history")
            keep = set(refs["run_ids"])
            protected_messages = set(refs["message_ids"])
            completed = sorted((r for r in runs if r.status == "completed"),
                               key=lambda r: (r.ended_at or r.created_at, r.id), reverse=True)
            keep.update(r.id for r in completed[:policy["keep_runs"]])
            for run in runs:
                sources.append(record(run))
                source = run.metadata.get("resume", {}).get("run_id")
                if source:
                    keep.add(source)
                if run.status != "completed":
                    for name in run.metadata.get("checkpoints", []):
                        checkpoint = self.manager.tasks.run_repository.checkpoint(run, name)
                        sources.append(checkpoint)
                        protected_messages.update(checkpoint["header"].get("message_ids", []))
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
                if policy["max_tokens"] is not None:
                    if self.counter is None:
                        raise ValueError("Retention requires a registered token counter")
                    tokens = self.counter({"model": project.config.completion.get("model"),
                        "messages": [{"role": m.role.value, "content": m.content} for m in owned]})
                    if type(tokens) is not int or tokens < 0:
                        raise ValueError("Retention counter requires nonnegative integer tokens")
                total_bytes += size
                total_tokens += tokens
                row = {"task_id": task.id, "run_id": run.id, "message_ids": [m.id for m in owned],
                       "message_revisions": {m.id: revision_token(record(m)) for m in owned},
                       "bytes": size, "tokens": tokens, "last_activity": run.ended_at or run.created_at,
                       "revision": revision_token(stamps), "record_revision": revision_token(record(run))}
                blocked = list(reasons)
                if run.status != "completed":
                    blocked.append("unfinished_or_resumable_run")
                if run.id in keep:
                    blocked.append("recent_or_referenced")
                usage = project.config.policies["usage"]
                if usage["project_max_calls"] is not None or usage["project_max_tokens"] is not None:
                    if any(usage["period_seconds"] is None or (clock - datetime.fromisoformat(e["started_at"])).total_seconds() <= usage["period_seconds"]
                           for e in run.metadata.get("completions", [])):
                        blocked.append("active_usage_accounting")
                (protected if blocked else rows).append({**row, **({"reasons": blocked} if blocked else {})})
        left_bytes, left_tokens, selected = total_bytes, total_tokens, []
        for row in sorted(rows, key=lambda r: (r["last_activity"], r["task_id"], r["run_id"])):
            expired = policy["max_age_seconds"] is not None and (clock - datetime.fromisoformat(row["last_activity"])).total_seconds() > policy["max_age_seconds"]
            if expired or policy["max_bytes"] is not None and left_bytes > policy["max_bytes"] or policy["max_tokens"] is not None and left_tokens > policy["max_tokens"]:
                selected.append(row)
                left_bytes -= row["bytes"]
                left_tokens -= row["tokens"]
        result = {"policy": policy, "candidates": selected, "protected": protected, "estimated": True,
                  "bytes_before": total_bytes, "bytes_after": left_bytes, "tokens_before": total_tokens, "tokens_after": left_tokens}
        return RetentionPlan(ResourceRef("project", project.id, project_id=project.id),
                             **result, version=revision_token({**result, "sources": sources}))

    def _finish(self, task, path):
        intent = read_json(reject_links(path))
        if intent.get("task_id") != task.id or intent.get("kind") != "run_retention":
            raise ValueError("Invalid retention journal")
        if self.manager.ownership.task_attached((task.project_id, task.id)):
            raise ValueError("Shut down Task runtime before retention recovery")
        repository = self.manager.tasks.run_repository
        # rmtree 도중 종료되어 run.json만 사라진 경우도, 승인된 삭제 저널을 통해 마무리한다.
        repository.recover_history_deletions(task, [r["run_id"] for r in intent["runs"]])
        for row in intent["runs"]:
            try:
                run = repository.load(task, row["run_id"])
            except FileNotFoundError:
                continue
            if run.status != "completed" or revision_token(record(run)) != row["record_revision"]:
                raise ValueError("Retention source changed; manual review is required")
        # 의도를 먼저 저장한 뒤 대화를 정리한다. 중단 시 runtime 재접속을 차단한다.
        store = self.manager.tasks.conversations(task)
        versions = {mid: version for row in intent["runs"] for mid, version in row["message_revisions"].items()}
        for message in store.list():
            if message.id in versions and revision_token(record(message)) != versions[message.id]:
                raise ValueError("Retention conversation changed; manual review is required")
        store.prune(set(versions))
        for row in intent["runs"]:
            repository.delete_history(task, row["run_id"])
        from llm.services.infrastructure.storage import child
        receipt = child(task.paths.state / "retention_history", intent["id"]).with_suffix(".json")
        atomic_json(reject_links(receipt), {**intent, "completed_at": now()})
        path.unlink()
        sync_directory(path.parent)
        return intent["id"]

    def plan(self, project) -> RetentionPlan:
        if project.config.policies["retention"]["unit"] == "run":
            return self._run_plan(project)
        policy = project.config.policies["retention"]
        refs = self._references(project)
        items, protected = [], []
        total_bytes = total_tokens = 0
        now = datetime.now(timezone.utc)
        for task in self.manager.tasks.list(project, include_deleted=True):
            runs = self.manager.tasks.run_repository.list(task)
            paths = list(task.paths.root.rglob("*"))
            if any(p.is_symlink() for p in paths):
                raise ValueError("Retention cannot inspect linked files")
            # 조회 자체가 운영 로그를 쓰므로 정책 대상은 도메인 기록/산출물이다. 로그 회전은 logger 책임이다.
            stamps = {}
            for path in paths:
                relative = path.relative_to(task.paths.root)
                if path.is_file() and "logs" not in relative.parts:
                    stat = path.stat()
                    stamps[str(relative)] = [stat.st_ino, stat.st_size, stat.st_mtime_ns]
            size = sum(stamp[1] for stamp in stamps.values())
            messages = self.manager.tasks.conversations(task).list()
            tokens = 0
            if policy["max_tokens"] is not None:
                if self.counter is None:
                    raise ValueError("Retention requires a registered token counter")
                tokens = self.counter({"model": project.config.completion.get("model"),
                    "messages": [{"role": m.role.value, "content": m.content} for m in messages]})
                if type(tokens) is not int or tokens < 0:
                    raise ValueError("Retention counter requires nonnegative integer tokens")
            total_bytes += size
            total_tokens += tokens
            reasons = []
            if task.id in refs["task_ids"] or any(r.id in refs["run_ids"] for r in runs) or any(m.id in refs["message_ids"] for m in messages):
                reasons.append("component_reference")
            if any((task.paths.state / "maintenance").glob("*.json")):
                reasons.append("pending_maintenance")
            if self.manager.ownership.task_attached((project.id, task.id)):
                reasons.append("runtime_attached")
            if task.current_run_id or any(r.status != "completed" for r in runs):
                reasons.append("unfinished_or_resumable_run")
            completed = {r.id for r in runs if r.status == "completed"}
            if any(m.status in ("queued", "streaming") or m.status == "committed" and m.run_id not in completed for m in messages):
                reasons.append("pending_message")
            if (task.paths.state / "tool_operations").exists():
                reasons.append("external_operation_history")
            if task.metadata.get("retain"):
                reasons.append("pinned")
            if project.conversation_storage != "file":
                reasons.append("non_file_conversation")
            usage = project.config.policies["usage"]
            if usage["project_max_calls"] is not None or usage["project_max_tokens"] is not None:
                period = usage["period_seconds"]
                if any(period is None or (now - datetime.fromisoformat(e["started_at"])).total_seconds() <= period
                       for r in runs for e in r.metadata.get("completions", [])):
                    reasons.append("active_usage_accounting")
            end = max([r.ended_at or r.created_at for r in runs] + [task.created_at])
            row = {"task_id": task.id, "bytes": size, "tokens": tokens, "last_activity": end,
                   "revision": revision_token(stamps)}
            if reasons:
                protected.append({**row, "reasons": reasons})
            else:
                items.append(row)
        remaining_bytes, remaining_tokens, selected = total_bytes, total_tokens, []
        for row in sorted(items, key=lambda r: (r["last_activity"], r["task_id"])):
            expired = policy["max_age_seconds"] is not None and (now - datetime.fromisoformat(row["last_activity"])).total_seconds() > policy["max_age_seconds"]
            oversize = policy["max_bytes"] is not None and remaining_bytes > policy["max_bytes"]
            overtokens = policy["max_tokens"] is not None and remaining_tokens > policy["max_tokens"]
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
        if plan.policy["unit"] == "run":
            grouped = {}
            for row in plan.candidates:
                grouped.setdefault(row["task_id"], []).append(row)
            for identifier, rows in grouped.items():
                task = self.manager.tasks.load(project, identifier)
                self.manager.tasks.require_inactive(task)
                path = reject_links(task.paths.state / "maintenance" / "retention.json")
                atomic_json(path, {"id": new_id(), "kind": "run_retention", "task_id": task.id,
                                   "created_at": now(), "runs": rows})
                self._finish(task, path)
            return plan
        for row in plan.candidates:
            task = self.manager.tasks.load(project, row["task_id"])
            self.manager.tasks.delete(task, permanent=True)
        return plan

    def recover(self, project):
        """이미 승인·기록된 삭제 의도만 완료한다. 새 보관 후보는 선택하지 않는다."""
        completed = []
        for task in self.manager.tasks.list(project, include_deleted=True):
            path = task.paths.state / "maintenance" / "retention.json"
            if path.exists():
                completed.append(self._finish(task, path))
        return completed

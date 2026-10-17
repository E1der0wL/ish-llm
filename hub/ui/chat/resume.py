"""One resume admission/feedback path for the input key and request history."""


def resume_turn(controller, session_id, run_id, request_id=None):
    view = controller.view
    key = (view.project_id, session_id)
    if session_id in controller._submitting or key in view.preparing.pending:
        return
    controller._submitting.add(session_id)
    view.preparing.start(key, resume_run_id=run_id, turn_id=request_id)

    def completed(result):
        controller._submitting.discard(session_id)
        if result is None:
            view.preparing.finish(key)
        controller._call("snapshot", completed=controller._snapshot)

    if request_id is None:
        controller._call("resume_latest", session_id, completed=completed)
    else:
        controller._call("retry_turn", session_id, request_id, run_id, completed=completed)

"""One resume admission/feedback path for the input key and request history."""

from hub.backend.runtime import HubRuntime


class ResumeFeedback:
    ABSENT = None


def resume_turn(controller, session_id, run_id, request_id=ResumeFeedback.ABSENT):
    view = controller.view
    key = (view.project_id, session_id)
    if session_id in controller._submitting or key in view.preparing.pending:
        return
    controller._submitting.add(session_id)
    view.preparing.start(key, resume_run_id=run_id, turn_id=request_id)

    def completed(result):
        controller._submitting.discard(session_id)
        if result is ResumeFeedback.ABSENT:
            view.preparing.finish(key)
        controller._call(HubRuntime.snapshot.__name__, completed=controller._snapshot)

    if request_id is ResumeFeedback.ABSENT:
        controller._call(
            HubRuntime.resume_latest.__name__, session_id, completed=completed
        )
    else:
        controller._call(
            HubRuntime.retry_turn.__name__,
            session_id,
            request_id,
            run_id,
            completed=completed,
        )

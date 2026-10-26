from annotation.workflow.control import RunControl, RunStopped


def test_run_control_is_idempotent_and_raises_after_stop():
    control = RunControl()

    assert control.stop_requested is False
    assert control.request_stop() is True
    assert control.request_stop() is False
    assert control.stop_requested is True

    try:
        control.raise_if_stopped()
    except RunStopped as exc:
        assert str(exc) == "user_requested_stop"
    else:  # pragma: no cover
        raise AssertionError("stopped run must raise RunStopped")

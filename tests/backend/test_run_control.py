from annotation.workflow.control import RunControl, RunStopped


def test_run_control_is_idempotent_and_raises_after_stop(tmp_path):
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


def test_run_control_does_not_stop_during_publish_and_writes_atomic_checkpoint(tmp_path):
    control = RunControl("run-test", root=tmp_path)
    control.committing = True
    assert control.request_stop() is False
    assert control.stop_requested is False
    control.committing = False
    control.checkpoint("stage", {"completed": 1})
    files = list((tmp_path / "run-test").glob("*.json"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8").find('"completed": 1') >= 0

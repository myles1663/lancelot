import sys
import types

from src.core.onboarding_snapshot import OnboardingSnapshot, OnboardingState
from src.ui import recovery_panel


class FakeColumn:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class FakeStreamlit(types.SimpleNamespace):
    def __init__(self, clicked=()):
        super().__init__()
        self.clicked = set(clicked)
        self.calls = []
        self.session_state = {}

    def _record(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def subheader(self, *args, **kwargs):
        self._record("subheader", *args, **kwargs)

    def progress(self, *args, **kwargs):
        self._record("progress", *args, **kwargs)

    def columns(self, count):
        self._record("columns", count)
        return [FakeColumn() for _ in range(count)]

    def metric(self, *args, **kwargs):
        self._record("metric", *args, **kwargs)

    def success(self, *args, **kwargs):
        self._record("success", *args, **kwargs)

    def warning(self, *args, **kwargs):
        self._record("warning", *args, **kwargs)

    def info(self, *args, **kwargs):
        self._record("info", *args, **kwargs)

    def error(self, *args, **kwargs):
        self._record("error", *args, **kwargs)

    def divider(self):
        self._record("divider")

    def caption(self, *args, **kwargs):
        self._record("caption", *args, **kwargs)

    def button(self, label, key=None, **kwargs):
        self._record("button", label, key=key, **kwargs)
        return key in self.clicked

    def rerun(self):
        self._record("rerun")


def test_render_recovery_panel_noops_when_streamlit_missing(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "streamlit", None)
    recovery_panel.render_recovery_panel(str(tmp_path))


def test_render_recovery_panel_shows_no_actions_at_welcome(monkeypatch, tmp_path):
    fake_st = FakeStreamlit()
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    recovery_panel.render_recovery_panel(str(tmp_path))

    assert any(call[0] == "subheader" for call in fake_st.calls)
    assert any(call[0] == "info" and "No recovery actions" in call[1][0] for call in fake_st.calls)


def test_render_recovery_panel_executes_clicked_action_and_reruns(monkeypatch, tmp_path):
    snap = OnboardingSnapshot(str(tmp_path))
    snap.transition(OnboardingState.FLAGSHIP_SELECTION)

    fake_st = FakeStreamlit(clicked={"recovery_back"})
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    recovery_panel.render_recovery_panel(str(tmp_path))

    assert any(call[0] == "button" and call[1][0] == "Go Back" for call in fake_st.calls)
    assert any(call[0] == "info" and "Moved back" in call[1][0] for call in fake_st.calls)
    assert "recovery_result" not in fake_st.session_state
    assert any(call[0] == "rerun" for call in fake_st.calls)


def test_render_recovery_panel_shows_and_clears_previous_result(monkeypatch, tmp_path):
    fake_st = FakeStreamlit()
    fake_st.session_state["recovery_result"] = "Moved back to Welcome"
    monkeypatch.setitem(sys.modules, "streamlit", fake_st)

    snap = OnboardingSnapshot(str(tmp_path))
    snap.transition(OnboardingState.READY, credential_status="verified")

    recovery_panel.render_recovery_panel(str(tmp_path))

    assert any(call[0] == "success" for call in fake_st.calls)
    assert any(call[0] == "info" and call[1][0] == "Moved back to Welcome" for call in fake_st.calls)
    assert "recovery_result" not in fake_st.session_state

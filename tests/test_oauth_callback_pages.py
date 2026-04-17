from src.core.oauth_callback_pages import (
    render_callback_exception_page,
    render_callback_page,
)


def test_callback_error_description_is_escaped():
    response = render_callback_page(
        "Authorization Failed",
        "<script>alert(1)</script>",
        status_code=400,
    )

    assert response.status_code == 400
    assert "<script>alert(1)</script>" not in response.body.decode()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in response.body.decode()


def test_callback_exception_page_hides_internal_error_text():
    response = render_callback_exception_page("Codex OAuth")
    body = response.body.decode()

    assert response.status_code == 500
    assert "Codex OAuth manager not initialized" not in body
    assert "could not complete the Codex OAuth callback" in body

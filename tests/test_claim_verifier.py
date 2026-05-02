from src.core.response.claim_verifier import ClaimVerifier


def test_verify_handles_empty_text_and_text_without_claims():
    verifier = ClaimVerifier()

    assert verifier.verify("", []).is_clean is True
    result = verifier.verify("Here is what I found in the logs.", [])

    assert result.is_clean is True
    assert result.cleaned_text == "Here is what I found in the logs."


def test_verify_accepts_claims_with_matching_successful_receipts():
    verifier = ClaimVerifier()
    text = "I sent the update. I searched the docs."
    receipts = [
        {"skill": "telegram_send", "result": "SUCCESS"},
        {"skill": "network_client", "result": "SUCCESS"},
    ]

    result = verifier.verify(text, receipts)

    assert result.is_clean is True
    assert result.flagged_claims == []
    assert result.cleaned_text == text


def test_verify_flags_failed_or_missing_receipts_and_removes_unverified_sentences():
    verifier = ClaimVerifier()
    text = "I sent the update. The summary is below. I deployed the service."
    receipts = [
        {"skill": "telegram_send", "result": "FAILED"},
        {"skill": "service_runner", "result": "EXCEPTION"},
    ]

    result = verifier.verify(text, receipts)

    assert result.is_clean is False
    assert "'sent'" in result.flagged_claims[0]
    assert "'deployed'" in result.flagged_claims[1]
    assert result.cleaned_text == "The summary is below."


def test_single_sentence_claim_is_softened_instead_of_removed():
    verifier = ClaimVerifier()

    result = verifier.verify("I successfully wrote the file.", [])

    assert result.is_clean is False
    assert result.cleaned_text == "I was unable to confirm that I wrote the file."


def test_mixed_verified_and_unverified_claims_keep_verified_sentence():
    verifier = ClaimVerifier()
    text = "I emailed the report. I deleted the temp file. Done."

    result = verifier.verify(text, [{"skill": "email.send", "result": "SUCCESS"}])

    assert result.is_clean is False
    assert result.cleaned_text == "I emailed the report. Done."


def test_claim_extraction_tracks_spans_and_all_action_verbs():
    verifier = ClaimVerifier()
    text = (
        "I already created the doc. I just ran the command. "
        "I notified the team. I scheduled the job."
    )

    claims = verifier._extract_claims(text)

    assert [claim.verb for claim in claims] == ["created", "ran", "notified", "scheduled"]
    assert all(text[start:end].lower().startswith("i") for start, end in [claim.span for claim in claims])

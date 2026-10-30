"""Domain tests: the access rules, exercised without HTTP."""

import json
from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest

from app.domain.errors import (
    BadRequest,
    Forbidden,
    InvalidTransition,
    NotFound,
    QcNotPassed,
)
from app.domain.models import QcStatus

UNKNOWN_ID = "00000000-0000-4000-8000-000000000000"


def _params(url):
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    file_id = parsed.path.rsplit("/", 1)[-1]
    return file_id, q["user"][0], int(q["exp"][0]), q["sig"][0]


# --- download authorization -----------------------------------------------------------------


def test_owner_can_download_passed_file(service, make_sample, pass_qc, clock, alice):  # D1
    f = make_sample().files[0]
    pass_qc(f.id)

    signed = service.request_download(alice, f.id)

    assert signed.expires_at == clock.now() + timedelta(seconds=300)
    file_id, user, exp, sig = _params(signed.url)
    assert (file_id, user) == (f.id, alice.id)
    assert service.verify_download(file_id, user, exp, sig).id == f.id


def test_granted_user_can_download_passed_file(service, make_sample, pass_qc, alice, bob):  # D2
    s = make_sample()
    service.grant_access(alice, s.id, "bob")
    pass_qc(s.files[0].id)

    signed = service.request_download(bob, s.files[0].id)

    assert _params(signed.url)[1] == bob.id


def test_ungranted_user_gets_not_found(service, make_sample, pass_qc, mallory):  # D3
    f = make_sample().files[0]
    pass_qc(f.id)

    with pytest.raises(NotFound):
        service.request_download(mallory, f.id)


@pytest.mark.parametrize("status", [QcStatus.PENDING, QcStatus.FAILED])
def test_owner_cannot_download_file_that_has_not_passed(service, make_sample, alice, status):
    # D4, D5
    f = make_sample().files[0]
    if status is not QcStatus.PENDING:
        service.update_qc(f.id, status)

    with pytest.raises(QcNotPassed) as exc:
        service.request_download(alice, f.id)
    assert exc.value.status is status
    assert exc.value.code == f"qc_{status}"


def test_ungranted_user_cannot_learn_qc_status(service, make_sample, mallory):  # D6
    f = make_sample().files[0]  # pending

    with pytest.raises(NotFound):
        service.request_download(mallory, f.id)


def test_unknown_file_is_indistinguishable_from_ungranted(service, make_sample, pass_qc, mallory):
    # D7
    f = make_sample().files[0]
    pass_qc(f.id)

    with pytest.raises(NotFound) as unknown:
        service.request_download(mallory, UNKNOWN_ID)
    with pytest.raises(NotFound) as ungranted:
        service.request_download(mallory, f.id)
    assert (unknown.value.code, unknown.value.message) == (
        ungranted.value.code,
        ungranted.value.message,
    )


def test_grant_covers_all_files_including_later_qc_changes(
    service, make_sample, pass_qc, alice, bob
):  # D8
    s = make_sample(files=("a.bam", "b.bam"))
    service.grant_access(alice, s.id, "bob")
    for f in s.files:
        pass_qc(f.id)  # QC passes after the grant was made

    for f in s.files:
        assert service.request_download(bob, f.id)


def test_grant_is_scoped_to_one_sample(service, make_sample, pass_qc, alice, bob):  # D9
    a = make_sample(name="a")
    b = make_sample(name="b")
    service.grant_access(alice, a.id, "bob")
    pass_qc(b.files[0].id)

    with pytest.raises(NotFound):
        service.request_download(bob, b.files[0].id)


# --- QC state machine -----------------------------------------------------------------------


@pytest.mark.parametrize("status", [QcStatus.PASSED, QcStatus.FAILED])
def test_qc_pending_to_terminal(service, make_sample, clock, status):  # D10
    f = make_sample().files[0]

    updated, changed = service.update_qc(f.id, status)

    assert changed is True
    assert updated.qc_status is status
    assert updated.qc_updated_at == clock.now()


@pytest.mark.parametrize(
    ("first", "second"),
    [(QcStatus.PASSED, QcStatus.FAILED), (QcStatus.FAILED, QcStatus.PASSED)],
)
def test_qc_terminal_states_cannot_change(service, make_sample, first, second):  # D11
    f = make_sample().files[0]
    service.update_qc(f.id, first)

    with pytest.raises(InvalidTransition):
        service.update_qc(f.id, second)
    assert service.repo.get_file(f.id).qc_status is first


@pytest.mark.parametrize("status", [QcStatus.PASSED, QcStatus.FAILED])
def test_repeated_qc_callback_is_idempotent(service, make_sample, clock, status):  # D12
    f = make_sample().files[0]
    first, _ = service.update_qc(f.id, status)
    clock.advance(60)

    again, changed = service.update_qc(f.id, status)

    assert changed is False
    assert again.qc_status is status
    assert again.qc_updated_at == first.qc_updated_at  # untouched by the retry


def test_qc_cannot_be_set_back_to_pending(service, make_sample):
    f = make_sample().files[0]

    with pytest.raises(BadRequest):
        service.update_qc(f.id, QcStatus.PENDING)


def test_qc_update_for_unknown_file(service):  # D13
    with pytest.raises(NotFound):
        service.update_qc(UNKNOWN_ID, QcStatus.PASSED)


# --- granting -------------------------------------------------------------------------------


def test_grantee_cannot_grant(service, make_sample, alice, bob, mallory):  # D14
    s = make_sample()
    service.grant_access(alice, s.id, "bob")

    with pytest.raises(Forbidden):
        service.grant_access(bob, s.id, "mallory")


def test_stranger_cannot_grant_or_discover_sample(service, make_sample, alice, mallory):  # D15
    s = make_sample()

    with pytest.raises(NotFound):
        service.grant_access(mallory, s.id, "mallory")
    with pytest.raises(NotFound):
        service.grant_access(alice, UNKNOWN_ID, "mallory")


def test_owner_cannot_grant_to_self(service, make_sample, alice):  # D16
    s = make_sample()

    with pytest.raises(BadRequest):
        service.grant_access(alice, s.id, "alice")


def test_granting_twice_is_idempotent(service, make_sample, clock, alice, bob):  # D17
    s = make_sample()
    first, created_first = service.grant_access(alice, s.id, "bob")
    clock.advance(60)

    second, created_second = service.grant_access(alice, s.id, "bob")

    assert (created_first, created_second) == (True, False)
    assert second == first  # original grant returned, timestamp unchanged


def test_grant_to_unknown_username(service, make_sample, alice):
    s = make_sample()

    with pytest.raises(NotFound) as exc:
        service.grant_access(alice, s.id, "nobody")
    assert exc.value.code == "user_not_found"


# --- revocation -----------------------------------------------------------------------------


def test_revoke_removes_access_and_kills_issued_urls(
    service, make_sample, pass_qc, alice, bob
):  # R1
    s = make_sample()
    service.grant_access(alice, s.id, "bob")
    pass_qc(s.files[0].id)
    issued = _params(service.request_download(bob, s.files[0].id).url)

    service.revoke_access(alice, s.id, bob.id)

    with pytest.raises(NotFound):
        service.request_download(bob, s.files[0].id)
    with pytest.raises(NotFound):
        service.verify_download(*issued)  # URL issued before revocation no longer works
    assert service.list_samples(bob) == []


def test_revoke_missing_grant_is_not_found(service, make_sample, alice, bob):  # R2
    s = make_sample()

    with pytest.raises(NotFound) as exc:
        service.revoke_access(alice, s.id, bob.id)
    assert exc.value.code == "grant_not_found"


def test_grantee_cannot_revoke(service, make_sample, alice, bob, mallory):  # R3
    s = make_sample()
    service.grant_access(alice, s.id, "bob")
    service.grant_access(alice, s.id, "mallory")

    with pytest.raises(Forbidden):
        service.revoke_access(bob, s.id, mallory.id)


# --- admin ----------------------------------------------------------------------------------


def test_admin_can_manage_access_on_any_sample(service, make_sample, admin, bob):  # R4
    s = make_sample()

    service.grant_access(admin, s.id, "bob")
    assert [g.username for g in service.list_grants(admin, s.id)] == ["bob"]
    service.revoke_access(admin, s.id, bob.id)
    assert service.list_grants(admin, s.id) == []


def test_admin_sees_every_sample_but_cannot_download_without_grant(
    service, make_sample, pass_qc, admin
):  # R5
    s = make_sample()
    pass_qc(s.files[0].id)

    sample, access = service.get_sample(admin, s.id)
    assert (sample.id, access) == (s.id, "none")
    assert [x.id for x in service.list_all_samples(admin)] == [s.id]
    with pytest.raises(NotFound):
        service.request_download(admin, s.files[0].id)

    service.grant_access(admin, s.id, admin.username)  # explicit, audited self-grant
    assert service.request_download(admin, s.files[0].id)
    granted = service.list_audit_events(admin, event="access_granted")[0]
    assert granted.details["as_admin"] is True


def test_admin_cannot_change_sample_content(service, make_sample, admin):  # R6
    s = make_sample()

    with pytest.raises(Forbidden):
        service.delete_sample(admin, s.id)
    with pytest.raises(Forbidden):
        service.delete_file(admin, s.files[0].id)


def test_admin_only_operations_reject_users(service, alice):
    with pytest.raises(Forbidden):
        service.list_all_samples(alice)
    with pytest.raises(Forbidden):
        service.list_audit_events(alice)


# --- samples and files ----------------------------------------------------------------------


@pytest.mark.parametrize("files", [(), ("a.bam", "b.bam", "c.vcf")])
def test_register_sample_files_start_pending(service, make_sample, alice, files):  # D18
    s = make_sample(files=files)

    stored, access = service.get_sample(alice, s.id)
    assert (stored.owner_id, access) == (alice.id, "owner")
    assert [f.filename for f in stored.files] == list(files)
    assert all(f.qc_status is QcStatus.PENDING for f in stored.files)
    assert len({f.id for f in stored.files}) == len(files)


def test_register_rejects_duplicate_or_unsafe_filenames(service, alice):
    with pytest.raises(BadRequest):
        service.register_sample(alice, "s", ["a.bam", "a.bam"])
    with pytest.raises(BadRequest):
        service.register_sample(alice, "s", ["../etc/passwd"])


def test_list_samples_shows_owned_and_shared(service, make_sample, alice, bob):
    mine = make_sample(owner=bob, name="bobs")
    shared = make_sample(name="alices")
    make_sample(name="private")
    service.grant_access(alice, shared.id, "bob")

    listed = {s.id: s.access for s in service.list_samples(bob)}

    assert listed == {mine.id: "owner", shared.id: "grantee"}


def test_get_sample_hidden_from_strangers(service, make_sample, alice, bob, mallory):
    s = make_sample()
    service.grant_access(alice, s.id, "bob")

    assert service.get_sample(bob, s.id)[1] == "grantee"
    with pytest.raises(NotFound):
        service.get_sample(mallory, s.id)


def test_only_owner_deletes_files_and_samples(service, make_sample, alice, bob, mallory):
    s = make_sample()
    service.grant_access(alice, s.id, "bob")

    with pytest.raises(Forbidden):  # grantee can see it, so 403
        service.delete_file(bob, s.files[0].id)
    with pytest.raises(NotFound):  # stranger can't, so 404
        service.delete_file(mallory, s.files[0].id)

    service.delete_file(alice, s.files[0].id)
    assert [f.id for f in service.get_sample(alice, s.id)[0].files] == [s.files[1].id]

    service.delete_sample(alice, s.id)
    with pytest.raises(NotFound):
        service.get_sample(alice, s.id)
    assert service.repo.list_grants(s.id) == []  # cascaded


# --- audit ----------------------------------------------------------------------------------


def test_audit_records_ids_but_never_names(service, make_sample, pass_qc, alice, admin):
    s = make_sample(files=("secret-patient-name.bam",), name="Patient X tumour")
    pass_qc(s.files[0].id)
    service.request_download(alice, s.files[0].id)

    events = service.list_audit_events(admin)
    assert {"sample_registered", "qc_status_changed", "download_url_issued"} <= {
        e.event for e in events
    }
    dumped = json.dumps([e.details for e in events])
    assert "secret-patient-name" not in dumped
    assert "Patient X" not in dumped
    assert "sig=" not in dumped

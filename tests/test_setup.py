import pytest

from inbox_to_action.setup import page_id_from, run_setup

PAGE = "https://www.notion.so/myspace/Inbox-to-Action-1a2b3c4d5e6f708192a3b4c5d6e7f809?pvs=4"


def test_page_id_from_url_or_id():
    assert page_id_from(PAGE) == "1a2b3c4d5e6f708192a3b4c5d6e7f809"
    assert page_id_from("1a2b3c4d-5e6f-7081-92a3-b4c5d6e7f809") == "1a2b3c4d5e6f708192a3b4c5d6e7f809"
    with pytest.raises(ValueError):
        page_id_from("not a page")


def test_run_setup_flows_values_to_vercel():
    answers = iter([PAGE, "priya@acme.example, dana@big.example"])
    secrets = iter(["ntn_secret", "sk-ant-secret", "a long passphrase!", "a long passphrase!"])
    created, saved, out = [], {}, []
    run_setup(ask=lambda q: next(answers), ask_secret=lambda q: next(secrets),
              create_db=lambda token, page: created.append((token, page)) or "db123",
              set_env=lambda name, value: saved.__setitem__(name, value), say=out.append)
    assert created == [("ntn_secret", "1a2b3c4d5e6f708192a3b4c5d6e7f809")]
    assert saved == {"NOTION_TOKEN": "ntn_secret", "NOTION_DATABASE_ID": "db123", "ANTHROPIC_API_KEY": "sk-ant-secret",
                     "APP_PASSWORD": "a long passphrase!", "VIP_EMAILS": "priya@acme.example,dana@big.example"}
    assert not any("secret" in line or "passphrase" in line for line in out)


def test_password_rules_reask():
    answers = iter([PAGE, ""])
    secrets = iter(["t", "k", "short", "long enough pass", "mismatch here!!", "long enough pass", "long enough pass"])
    saved, out = {}, []
    run_setup(ask=lambda q: next(answers), ask_secret=lambda q: next(secrets), create_db=lambda t, p: "db",
              set_env=lambda n, v: saved.__setitem__(n, v), say=out.append)
    assert saved["APP_PASSWORD"] == "long enough pass" and "VIP_EMAILS" not in saved
    assert out.count("Use at least 12 characters.") == 1 and out.count("Those didn't match, try again.") == 1


def test_notion_error_is_explained_not_a_traceback():
    from inbox_to_action.notion import NotionError
    answers = iter([PAGE, ""])
    secrets = iter(["bad", "k", "long enough pass", "long enough pass"])
    out, saved = [], {}
    def refuse(token, page):
        raise NotionError("Notion 401: unauthorized")
    with pytest.raises(SystemExit):
        run_setup(ask=lambda q: next(answers), ask_secret=lambda q: next(secrets), create_db=refuse,
                  set_env=lambda n, v: saved.__setitem__(n, v), say=out.append)
    assert saved == {} and any("Notion refused" in line and "401" in line for line in out)

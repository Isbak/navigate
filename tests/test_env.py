import os

from catalog.env import load_dotenv


def test_load_dotenv_reads_values_without_overriding_existing_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "EXISTING=from-file\n"
        "QUOTED=\"hello # not a comment\"\n"
        "export INLINE=kept # comment\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("EXISTING", "from-process")
    monkeypatch.delenv("QUOTED", raising=False)
    monkeypatch.delenv("INLINE", raising=False)

    assert load_dotenv(env_file) is True

    assert os.environ["EXISTING"] == "from-process"
    assert os.environ["QUOTED"] == "hello # not a comment"
    assert os.environ["INLINE"] == "kept"

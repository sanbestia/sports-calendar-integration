import pytest

from sports_calendar.files import write_atomically


def test_write_atomically_replaces_the_file(tmp_path):
    path = tmp_path / "token.json"
    path.write_text("old")

    write_atomically(str(path), "new")

    assert path.read_text() == "new"


def test_a_failed_write_keeps_the_old_file_and_leaves_no_temporary_file(tmp_path):
    """A token file cut in half stops every run; a temporary file left behind would hold part of a secret."""
    path = tmp_path / "token.json"
    path.write_text("old")

    with pytest.raises(TypeError):
        write_atomically(str(path), b"not text")  # fails inside the write, after the temporary file is open

    assert path.read_text() == "old"
    assert [p.name for p in tmp_path.iterdir()] == ["token.json"]

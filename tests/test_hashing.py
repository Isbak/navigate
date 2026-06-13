from catalog.hashing import document_id, sha256_file


def test_sha256_file_and_document_id(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("hello", encoding="utf-8")
    digest = sha256_file(path)
    assert digest == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert document_id(digest) == "doc_2cf24dba5fb0"

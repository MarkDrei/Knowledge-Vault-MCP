from knowledge_vault_mcp.auth.passwords import hash_password, verify_password


def test_roundtrip():
    h = hash_password("s3cret-password")
    assert "$" not in h
    assert verify_password("s3cret-password", h)
    assert not verify_password("wrong", h)


def test_garbage_hash_is_rejected():
    assert not verify_password("x", "not-a-hash")
    assert not verify_password("x", "bcrypt:1:2:3:YQ==:YQ==")

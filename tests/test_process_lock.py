from utils.process_lock import acquire_process_lock


def test_process_lock_allows_only_one_holder(tmp_path):
    first = acquire_process_lock("example", root=tmp_path)
    assert first is not None

    second = acquire_process_lock("example", root=tmp_path)
    assert second is None

    first.close()

    third = acquire_process_lock("example", root=tmp_path)
    assert third is not None
    third.close()

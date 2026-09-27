from pathlib import Path

from agenttx.layers import LayerStore


def test_unchanged_steps_share_a_snapshot_until_the_next_write(tmp_path: Path) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    (upper / "value.txt").write_text("old\n", encoding="utf-8")
    store = LayerStore(tmp_path / "layers")

    first = store.snapshot_before(0, upper)
    second = store.snapshot_before(1, upper, changed_paths=[])
    third = store.snapshot_before(2, upper, changed_paths=[])
    assert second.is_symlink()
    assert third.is_symlink()
    assert second.resolve() == first
    assert third.resolve() == first

    (upper / "value.txt").write_text("new\n", encoding="utf-8")
    fourth = store.snapshot_before(3, upper, changed_paths=["/value.txt"])
    assert fourth.is_dir() and not fourth.is_symlink()
    assert (fourth / "value.txt").read_text(encoding="utf-8") == "new\n"
    assert (third / "value.txt").read_text(encoding="utf-8") == "old\n"

    store.restore_paths(2, upper, ["/value.txt"])
    assert (upper / "value.txt").read_text(encoding="utf-8") == "old\n"


def test_dropping_a_shared_base_retains_it_until_last_alias(tmp_path: Path) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    (upper / "value.txt").write_text("old\n", encoding="utf-8")
    store = LayerStore(tmp_path / "layers")
    first = store.snapshot_before(0, upper)
    second = store.snapshot_before(1, upper, changed_paths=[])
    third = store.snapshot_before(2, upper, changed_paths=[])

    store.drop_from([0])
    assert first.is_dir()
    assert second.is_symlink()
    assert third.resolve() == first
    assert (store.root / ".retained_before_0000").exists()

    store.drop_from([1])
    assert not second.exists()
    assert third.is_symlink()
    (upper / "value.txt").write_text("bad\n", encoding="utf-8")
    store.restore_before(2, upper)
    assert (upper / "value.txt").read_text(encoding="utf-8") == "old\n"
    store.drop_from([2])
    assert not first.exists()
    assert not (store.root / ".retained_before_0000").exists()


def test_shared_snapshot_survives_layer_store_copy(tmp_path: Path) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    (upper / "value.txt").write_text("old\n", encoding="utf-8")
    original = LayerStore(tmp_path / "original")
    original.snapshot_before(0, upper)
    original.snapshot_before(1, upper, changed_paths=[])

    cloned_root = tmp_path / "cloned"
    original.copy_tree(original.root, cloned_root)
    cloned = LayerStore(cloned_root)
    assert (cloned.resolve_before(1) / "value.txt").read_text(
        encoding="utf-8"
    ) == "old\n"
    assert (cloned.root / "before_0001").is_symlink()


def test_broken_alias_does_not_clear_the_current_upperdir(tmp_path: Path) -> None:
    upper = tmp_path / "upper"
    upper.mkdir()
    (upper / "value.txt").write_text("live\n", encoding="utf-8")
    store = LayerStore(tmp_path / "layers")
    (store.root / "before_0001").symlink_to("before_0000", target_is_directory=True)

    try:
        store.restore_before(1, upper)
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("a dangling snapshot alias must fail closed")
    assert (upper / "value.txt").read_text(encoding="utf-8") == "live\n"

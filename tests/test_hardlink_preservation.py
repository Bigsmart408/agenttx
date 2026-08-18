from pathlib import Path
import os

from agenttx.commit_wal import _copy_host_entry
from agenttx.layers import _copy_overlay_tree
from agenttx.object_identity import (
    HardlinkCatalog,
    HardlinkTopologyError,
    discover_hardlink_group,
    expand_hardlink_paths,
)
from agenttx.ledger import Effect, EffectKind, Ledger


def _assert_same_object(left: Path, right: Path) -> None:
    left_stat = left.stat()
    right_stat = right.stat()
    assert (left_stat.st_dev, left_stat.st_ino) == (
        right_stat.st_dev,
        right_stat.st_ino,
    )
    assert left_stat.st_nlink == right_stat.st_nlink == 2


def test_overlay_tree_copy_preserves_hardlinks(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "first").write_text("old\n", encoding="utf-8")
    os.link(source / "first", source / "alias")
    destination = tmp_path / "destination"

    _copy_overlay_tree(source, destination)
    _assert_same_object(destination / "first", destination / "alias")
    (destination / "alias").write_text("new\n", encoding="utf-8")
    assert (destination / "first").read_text(encoding="utf-8") == "new\n"


def test_commit_wal_host_copy_preserves_hardlinks(tmp_path: Path) -> None:
    source = tmp_path / "host"
    source.mkdir()
    (source / "first").write_text("payload\n", encoding="utf-8")
    os.link(source / "first", source / "alias")
    destination = tmp_path / "backup"

    _copy_host_entry(source, destination)
    _assert_same_object(destination / "first", destination / "alias")


def test_identity_expansion_covers_complete_workspace_group(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "first").write_text("payload\n", encoding="utf-8")
    os.link(workspace / "first", workspace / "alias")

    paths, groups = expand_hardlink_paths([str(workspace / "first")], workspace)

    assert paths == [str(workspace / "alias"), str(workspace / "first")]
    assert len(groups) == 1
    assert groups[0].paths == tuple(paths)


def test_identity_expansion_fails_closed_for_external_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "first").write_text("payload\n", encoding="utf-8")
    external = tmp_path / "external"
    external.mkdir()
    os.link(workspace / "first", external / "alias")

    try:
        discover_hardlink_group(workspace / "first", workspace)
    except HardlinkTopologyError as exc:
        assert "cannot prove complete hard-link group" in str(exc)
    else:
        raise AssertionError("external alias must not be silently split")


def test_catalog_object_id_creates_alias_dependency(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first"
    alias = workspace / "alias"
    first.write_text("payload\n", encoding="utf-8")
    os.link(first, alias)

    catalog = HardlinkCatalog()
    catalog.refresh(workspace)
    writer = catalog.annotate(
        [Effect(str(first), EffectKind.WRITE)]
    )[0]
    reader = catalog.annotate(
        [Effect(str(alias), EffectKind.READ)]
    )[0]
    assert writer.object_id is not None
    assert reader.object_id == writer.object_id

    ledger = Ledger()
    ledger.add_step("writer", [writer])
    second = ledger.add_step("reader", [reader])
    assert second.parents == {0}


def test_catalog_roundtrip_keeps_object_id(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    first = workspace / "first"
    alias = workspace / "alias"
    first.write_text("payload\n", encoding="utf-8")
    os.link(first, alias)

    catalog = HardlinkCatalog()
    catalog.refresh(workspace)
    original = catalog.annotate([Effect(str(first), EffectKind.WRITE)])[0]
    restored = HardlinkCatalog.from_dict(catalog.to_dict())
    rebound = restored.annotate([Effect(str(alias), EffectKind.READ)])[0]
    assert rebound.object_id == original.object_id

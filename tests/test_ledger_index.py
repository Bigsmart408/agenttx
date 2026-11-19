import random

from agenttx.ledger import Effect, EffectKind, Ledger


def reference_parents(ledger, effects):
    parents = set()
    for old in ledger.active_uncommitted():
        for before in old.effects:
            for after in effects:
                relevant = before.kind in (EffectKind.WRITE, EffectKind.DELETE)
                relevant |= before.kind == EffectKind.NEGATIVE and after.kind == EffectKind.WRITE
                if relevant and (ledger._paths_overlap(before.path, after.path)
                                 or ledger._objects_overlap(before, after)):
                    parents.add(old.step_id)
    return parents


def test_index_preserves_reference_graph_with_aliases_frontiers_and_rollbacks():
    rng = random.Random(20261026)
    paths = ['', '/', '//', '///a', '//a/b', '/a', '/ab', '/a/b', '/a//b', '/a/b/',
             'a', 'a/b', '.', './a', '../a', '/é/文', '/é/文/子']
    kinds = list(EffectKind)
    for _ in range(15):
        ledger = Ledger()
        for index in range(45):
            effects = [Effect(rng.choice(paths), rng.choice(kinds), object_id=rng.choice([None, '', 'one', 'two']))
                       for _ in range(rng.randrange(9))]
            expected = reference_parents(ledger, effects)
            assert ledger.add_step('operation', effects).parents == expected
            if index and index % 9 == 0:
                ledger.steps[rng.randrange(index)].status = 'rolled_back'
            if index and index % 13 == 0:
                ledger.committed_frontier = index // 2
            if index % 7 == 0:
                ledger = Ledger.from_dict(ledger.to_dict())


def test_large_independent_package_file_sets_keep_their_exact_dependencies():
    ledger = Ledger()
    count = 10000
    ledger.add_step('package-a', [Effect(f'/site/a/f{i}', EffectKind.WRITE) for i in range(count)])
    independent = ledger.add_step('package-b', [Effect(f'/site/b/f{i}', EffectKind.WRITE) for i in range(count)])
    assert independent.parents == set()
    reader = ledger.add_step('read-parent', [Effect('/site', EffectKind.READ)])
    assert reader.parents == {0, 1}
    alias = ledger.add_step('alias', [Effect('/elsewhere', EffectKind.WRITE, object_id='shared')])
    linked = ledger.add_step('read-hardlink', [Effect('/other', EffectKind.READ, object_id='shared')])
    assert linked.parents == {alias.step_id}

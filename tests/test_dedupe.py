"""Guards for the 1.000 duplicate grouping and query-time collapse.

The threshold is the whole design (see dedupe.py). These assert the two
properties that make it safe, against the real corpora:

  * a group NEVER contains members that disagree on a figure -- if it did,
    collapsing would silently pick one manual's number over the other's;
  * collapse() removes repeats without reordering or dropping distinct hits,
    and what survives still carries the attribution needed to cite a manual.

Runs standalone (python tests/test_dedupe.py) or under pytest.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dedupe import CORPORA, annotate, clause_of, collapse, group, load  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "data" / "processed"


def built():
    corpora = load()
    groups = group(corpora)
    annotate(corpora, groups)
    return corpora, groups


def test_groups_never_disagree_on_a_figure():
    """The premise of collapsing: identical text implies identical figures."""
    _, groups = built()
    bad = []
    for gid, members in groups.items():
        figs = {tuple(sorted(set(c["numbers"]))) for c in members}
        units = {tuple(sorted(set(c["unit_numbers"]))) for c in members}
        if len(figs) > 1 or len(units) > 1:
            bad.append((gid, [c["chunk_id"] for c in members]))
    assert not bad, (
        "groups whose members disagree on a figure -- collapsing these would "
        f"silently drop one manual's number: {bad[:5]}")


def test_every_group_is_cross_manual():
    """Within-manual duplicates would mean the pipeline emitted a chunk twice."""
    _, groups = built()
    within = [gid for gid, m in groups.items()
              if len({c["_doc"] for c in m}) == 1]
    assert not within, f"same-manual duplicate groups: {within[:5]}"


def test_collapse_removes_repeats_and_keeps_order():
    corpora, groups = built()
    gid, members = next(iter(groups.items()))
    a, b = members[0], members[1]
    other = next(c for c in corpora[CORPORA[0]] if c["dup_group"] != gid)

    ranked = [(9.0, a), (8.5, b), (7.0, other)]
    kept = collapse(ranked, k=5)
    assert [c["chunk_id"] for _, c in kept] == [a["chunk_id"], other["chunk_id"]], \
        "collapse should drop the duplicate and keep the rest in order"
    assert [s for s, _ in kept] == [9.0, 7.0], "scores must be preserved"

    # the survivor still knows both manuals said it
    assert set(kept[0][1]["dup_sources"]) == {c["_doc"] for c in members}
    assert a["chunk_id"] in kept[0][1]["dup_members"]
    assert b["chunk_id"] in kept[0][1]["dup_members"]


def test_collapse_never_merges_singletons():
    """dup_group None must not collide -- every singleton is its own answer."""
    corpora, _ = built()
    singles = [c for tag in CORPORA for c in corpora[tag]
               if c["dup_group"] is None][:4]
    assert len(singles) >= 2, "expected singleton chunks in these corpora"
    kept = collapse([(9.0 - i, c) for i, c in enumerate(singles)], k=10)
    assert len(kept) == len(singles), \
        "singletons share dup_group None and must never collapse together"


def test_single_source_chunks_are_flagged_for_attribution():
    corpora, _ = built()
    for tag in CORPORA:
        for c in corpora[tag]:
            assert c["dup_sources"], f"{c['chunk_id']} has no dup_sources"
            if c["dup_group"] is None:
                assert c["dup_sources"] == [tag], (
                    f"{c['chunk_id']} is a singleton but claims sources "
                    f"{c['dup_sources']}")


def test_citations_cover_every_member():
    """15 groups span different clause numbers; both must be citable."""
    _, groups = built()
    multi = 0
    for gid, members in groups.items():
        c0 = members[0]
        cites = {(x["doc"], x["clause"]) for x in c0["dup_citations"]}
        assert len(c0["dup_citations"]) == len(members), (
            f"{gid}: {len(c0['dup_citations'])} citations for "
            f"{len(members)} members")
        for m in members:
            assert (m["_doc"], clause_of(m)) in cites, (
                f"{gid}: {m['chunk_id']} missing from dup_citations")
        if len({x["clause"] for x in c0["dup_citations"]}) > 1:
            multi += 1
    assert multi > 0, (
        "expected some groups to span different clause numbers -- if this "
        "hits zero the renumbering hazard has gone away and the citation "
        "machinery may no longer be needed")


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted((k, v) for k, v in globals().items()
                           if k.startswith("test_") and callable(v)):
        try:
            fn()
            print(f"PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL  {name}\n      {e}")
    print(f"\n{'FAILED' if fails else 'OK'} -- {fails} failure(s)")
    sys.exit(1 if fails else 0)

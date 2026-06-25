# Remove artifacts and clean up related knowledge

**Goal:** permanently remove one or more source files from the Navigate index,
cleaning up every piece of derived data — candidate extractions, knowledge
objects, graph relationships, compliance metadata, links, and extraction cache —
so that nothing from the removed content lingers in the system.

## Prerequisites

- A running Navigate setup with an initialised database. See
  [Catalog your files](catalog-your-files.md).
- Optional: a consolidated knowledge graph if you want to verify that graph
  objects are removed too. See [Build a knowledge graph](build-a-knowledge-graph.md).

---

## Choosing an approach

| Situation | Recommended approach |
| --- | --- |
| You deleted files from disk and just ran `scan` | [Re-consolidate after scan](#1-re-consolidate-after-a-scan-marks-files-deleted) |
| You want to permanently erase a file or folder from the index | [Use `clean-source`](#2-permanently-purge-with-clean-source) |
| The file exists at two paths (duplicate content) | [Read the duplicate note](#duplicates-shared-artifact-ids) |
| You only want to remove the graph objects, not the raw extraction | Unsupported — use `clean-source` and re-add the path if needed |

---

## 1. Re-consolidate after a scan marks files deleted

When a source file disappears from disk, `catalog scan` notices and marks it
`DELETED` in the index — but it keeps the raw `candidate_*` rows so the content
comes back automatically if the file is restored. To drop the knowledge objects
and graph edges derived from it, you only need to run consolidation:

```bash
catalog scan              # marks removed files DELETED
catalog consolidate       # drops knowledge objects from DELETED files
```

**What gets cleaned up**

- All knowledge objects whose only evidence came from the deleted file are
  removed (their `knowledge_mentions`, `knowledge_evidence`, and all
  `knowledge_relationships` edges cascade automatically).
- Standard-driven relationships (`mandated_by`, `specifies`, `appears_in`) that
  depended solely on the deleted file are dropped.
- Compliance metadata rows (`compliance_standards`, `compliance_requirements`,
  `compliance_equations`) for knowledge objects that no longer exist are removed.

**What is intentionally kept**

- The `artifacts` row with `scan_status = 'DELETED'` (so Navigate remembers the
  file existed).
- The `candidate_*` rows (so the content returns to the graph if the file comes
  back later).
- Links from the deleted file — run `clean-source` if you want those gone too.

If you are certain the file is gone for good, continue to
[`clean-source`](#2-permanently-purge-with-clean-source) to erase the raw rows
as well.

---

## 2. Permanently purge with `clean-source`

`clean-source` is the hard delete: it removes artifact rows, candidate
extractions, links, and the extraction cache for the target path, then
reconsolidates so every derived knowledge object and relationship disappears.

```bash
catalog clean-source --path PATH
```

`PATH` can be a **single file** or a **folder** (everything under it is purged).

**Example — remove one document:**

```bash
catalog clean-source --path ~/Documents/old-policy.pdf
```

**Example — remove an entire source folder:**

```bash
catalog clean-source --path ~/Documents/archived/
```

**What gets cleaned up**

| Layer | Cleaned up by `clean-source` |
| --- | --- |
| `artifacts` table row(s) | Yes — hard-deleted |
| `candidate_*` extraction rows | Yes — deleted for content IDs exclusive to this path |
| `links` table rows | Yes — deleted for exclusive content IDs |
| Extraction cache directory | Yes — `cache/<artifact_id>/` removed |
| `knowledge_objects` | Yes — rebuilt without the purged content |
| `knowledge_relationships` (all predicates including `mandated_by`, `specifies`, `appears_in`) | Yes — rebuilt without the purged content |
| `knowledge_mentions` / `knowledge_evidence` | Yes — cascade from `knowledge_objects` |
| `compliance_standards` / `compliance_requirements` / `compliance_equations` | Yes — orphaned rows deleted during reconsolidation |
| `compliance_assessments` | No — human assessment work is preserved |
| Governance metadata (`knowledge_owners`, `knowledge_lifecycle`, etc.) | No — curated governance data is preserved |

### Skip the reconsolidation step

If you are purging many paths in a batch and want to reconsolidate once at the
end:

```bash
catalog clean-source --path PATH_A --no-reconsolidate
catalog clean-source --path PATH_B --no-reconsolidate
catalog clean-source --path PATH_C --no-reconsolidate
catalog consolidate                        # rebuild the graph once
```

### Scope the reconsolidation

By default the reconsolidation after `clean-source` is scoped to your
configured source folders (from `config/sources.yml`), which is the recommended
setting. To rebuild from all material regardless of source configuration:

```bash
catalog clean-source --path PATH --all-sources
```

---

## Duplicates (shared artifact IDs)

Navigate uses content-addressed IDs: byte-identical files share the same
`artifact_id`. When you purge a path and a duplicate copy of the same content
exists elsewhere:

- The artifact row for the purged path is removed.
- The `candidate_*` rows are **kept** (the surviving copy still needs them).
- Knowledge objects derived from that content **persist** as long as the
  duplicate survives under a configured source folder.
- A warning is printed: `N artifact(s) kept: a duplicate copy lives outside PATH`.

To fully remove the content, purge all paths that hold it:

```bash
catalog clean-source --path ~/Documents/old-policy.pdf
catalog clean-source --path ~/Archive/old-policy.pdf
```

You can find all paths for a given file with `catalog show-duplicates`.

---

## Verify the cleanup

After purging, confirm nothing remains:

```bash
# Confirm the artifact row is gone
catalog stats

# Confirm no knowledge objects from the file remain
catalog knowledge-stats

# Check for any orphaned graph objects (should be 0 after clean-source)
catalog governance orphans

# Confirm the compliance layer is clean
catalog compliance list-standards
catalog compliance list-requirements
```

To inspect a specific object by its stable id before and after:

```bash
catalog show-object capability_old_capability    # returns nothing if correctly removed
```

---

## Quick-reference

```bash
# Soft removal — scan marks DELETED, consolidate cleans the graph
catalog scan
catalog consolidate

# Hard removal — erase everything for a file
catalog clean-source --path FILE

# Hard removal — erase everything under a folder
catalog clean-source --path FOLDER/

# Batch hard removal without intermediate reconsolidations
catalog clean-source --path A --no-reconsolidate
catalog clean-source --path B --no-reconsolidate
catalog consolidate

# Confirm cleanup
catalog governance orphans
catalog compliance list-standards
```

# Wikis

The Mu2e wikis are indexed as two sources:

| source | wiki | login |
|---|---|---|
| `mu2e-wiki` | <https://mu2ewiki.fnal.gov> (public) | none |
| `mu2e-internal-wiki` | <https://mu2einternalwiki.fnal.gov> | Fermilab SSO with `MU2E_DOCDB_USERNAME` / `MU2E_DOCDB_PASSWORD` (the DocDB account) |

Unlike a DocDB version, a wiki page changes in place. The knowledge base keeps **one current
copy per page**. A page's raw document is its lasting identity: it is matched on MediaWiki's
`pageid`, which survives renames, so visibility decisions and redactions stay with the page.
What was parsed from it (text, images, tables, chunks, embeddings) is rebuilt when the
revision changes. A page deleted from the wiki is removed. Older content is kept but marked:
every hit carries `LAST_EDITED`, and the tool description says to prefer a newer DocDB
document where the two disagree.

## Sync

```bash
kb-import wiki --sync --auth none --wiki-url https://mu2ewiki.fnal.gov --source-id mu2e-wiki          # dry run
kb-import wiki --sync --apply --auth sso --wiki-url https://mu2einternalwiki.fnal.gov \
    --source-id mu2e-internal-wiki
```

A sync lists every content page of `--namespaces` (default `0`, the main namespace; redirects,
talk, user, file and category pages are left out) with its current revision and compares the
list with the source:

| page | action |
|---|---|
| not in the source | added |
| revision changed, or renamed | re-imported into its existing raw document |
| unchanged | nothing (`--full`: re-imported anyway; template edits change pages without a revision of their own) |
| no longer in the wiki | removed with everything parsed from it |

**Guards.** Nothing is deleted if the page list could not be read completely, came back empty,
or would remove more than `--max-delete-fraction` (default 10%) of the known pages: a broken
listing (an expired login, an API error) must not look like a wiki that emptied. Before the
first page is imported, the image-description model is tried with a test image. If it fails
(an expired ALCF token, typically), the sync stops and imports nothing. A page is re-imported
only when it changes, so one imported with placeholder descriptions would keep them.

**What a page becomes.** The page HTML from the API, without the `[edit]` links and the
table of contents, with its images downloaded at full size and embedded so that the parser
sees their pixels. Docling's HTML backend turns that into a text document with tables and
image documents of their own (described, deduplicated through the image registry, inlined
into the text). It is chunked by **section** (`chunk_strategy="section"`, section paths like
`TrackerIntro > Requirements`) with the document gist prepended, and summarized like any
other document.

**Hits** carry the page address as `URI`, `LAST_EDITED` from the last revision, and
`SECTION_LINKS` to the sections the matched chunks came from (`…/wiki/Disks#Ceph_Transition`).

## Privacy

New pages are held (`PRIVACY_HOLD_NEW_DOCUMENTS`) until the privacy scan has read them, then
the policy releases or redacts them, as for DocDB. A changed page whose visibility was decided
automatically goes back on hold until its new content is scanned; a decision a person made is
kept, and the rescan's findings go to the review queue as flagged. See [Privacy](privacy.md).

## Scheduled sync

`kb-docdb-update.sh` syncs both wikis after the DocDB import and before the privacy step, so
new and changed pages are scanned and released in the same run. `WIKI_SYNCS` lists the
sources as `source_id|url|auth` entries (`WIKI_SYNCS=""` turns the step off). The run on the
morning of `WIKI_FULL_DAY` (ISO weekday, default 7 = Sunday) adds `--full`. A sync that fails,
or in which pages failed, logs `WIKI FAILED:` and fails the run; the DocDB part is unaffected.

# ECL attachments

The Mu2e electronic logbook's entries are served by the ECL MCP server. `kb-import ecl` makes
the **documents attached to them** searchable: pdf, xlsx, docx, pptx and text files (images
with `--include-images`).

```bash
kb-import ecl --days 2 --skip-existing          # recent entries (the scheduled run)
kb-import ecl --days 730 --skip-existing        # backfill
kb-import ecl --ids 6098,6100                   # given entries
```

- Source `mu2e-ecl`; one raw document per attachment, `doc_id` `<entry>-<stem>_<ext>`
  (`{"wildcard": {"doc_id": "6098-*"}}` finds an entry's attachments).
- URI: the entry page. Metadata: `ecl_entry`, `ecl_category`, `ecl_author`, `ecl_subject`,
  `ecl_tags`, `ecl_time`, `ecl_filename`, and the start of the entry text (`ecl_entry_text`).
- `--category` (repeatable) limits the entries; `--url` selects another experiment's logbook.
- Login: the Fermilab Services account in `MU2E_DOCDB_USERNAME` / `MU2E_DOCDB_PASSWORD`, as for
  DocDB.
- Private entries are skipped. New documents are held for the privacy scan like any other
  (see [Privacy](privacy.md)).

`kb-docdb-update.sh` runs `kb-import ecl --days 2 --skip-existing` after the DocDB import
(`ECL_DAYS=0` turns it off).

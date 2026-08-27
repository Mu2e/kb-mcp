# Database Schema

Schema-only `pg_dump` snapshot of the live database — every `CREATE TABLE` /
`CREATE INDEX` / constraint, no data. A static snapshot, not regenerated
automatically, so it can drift from the live schema; see
[Database Structure](../guides/database.md) for the code-derived (always
current) documentation instead.

```sql
--8<-- "docs/reference/schema.sql"
```

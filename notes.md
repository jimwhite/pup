TODO:  FOR NEXT NOTEBOOK INGESTION: The comment cells are including the fences.  Use the actual comment text span provenance info.

TODO: Give symbols that are defined in each cell.  Have symbol as tool param.  Matching for ingest.

TODO: Drop portcullis cells from summarization - they're not in the source it is for processing only.

TODO: Try groq gpt-oss-20b for speed.  We'll use an API key.
https://console.groq.com/docs/model/openai/gpt-oss-20b

TODO: We're gonna want to compare summaries from various models.  So we should have model and prompt properties on the summaries and the ability to select based on those.


defmacro brr@ (sym) in defthm.lisp is interesting case
http://127.0.0.1:5001/notebook/defthm.lisp?cell=399

It has a how summary which relatively few cells do.
"""
How: Usage: `(brr@ :field)` where `:field` is one of the recognized keywords (e.g., `:lhs`, `:target`). It evaluates to an expression that retrieves the corresponding value from the BRR wormhole state.
"""
That elides the list of valid sym values which a detailed HowTo would include but that isn't all that bad for us because at least RAG finds the cell and we can than then read the code.  

And at that point besides using the LLM we would rather have an exact answer so the question is how do we get the list of valid values of the argument(s) given an ACL2 function?  




sudo apt-get update
sudo apt-get install -y file hashalot git-lfs rsync

docker ps --filter "publish=8080" --format "{{.ID}} {{.Names}} {{.Image}}"

# Copy data out of the container
docker cp weaviate_local:/var/lib/weaviate data/weaviate-backup-$(date +%Y%m%d)




Weaviate has a built-in backup API, but it requires a backup module to be enabled when the server starts (e.g., `backup-filesystem`, `backup-s3`, `backup-gcs`). The error you got earlier — `"backup: filesystem not found"` — means none were enabled on your container.

**Two options:**

### Option 1: Enable the filesystem backup module (recommended going forward)

Restart the Weaviate container with the backup module enabled. On your host:

```bash
docker stop weaviate_local

docker run -d \
  --name weaviate_local \
  -p 8080:8080 -p 50051:50051 \
  -v weaviate_data:/var/lib/weaviate \
  -e PERSISTENCE_DATA_PATH=/var/lib/weaviate \
  -e ENABLE_MODULES=backup-filesystem \
  -e BACKUP_FILESYSTEM_PATH=/var/lib/weaviate/backups \
  <YOUR_WEAVIATE_IMAGE>
```

Then you can trigger backups via the API:
```bash
# Create a backup
curl -X POST http://localhost:8080/v1/backups/filesystem \
  -H 'Content-Type: application/json' \
  -d '{"id": "backup-20260228", "include": ["ACL2Notebook","ACL2Cell","ACL2Symbol","ACL2Summary"]}'

# Check status
curl http://localhost:8080/v1/backups/filesystem/backup-20260228

# Restore
curl -X POST http://localhost:8080/v1/backups/filesystem/backup-20260228/restore \
  -H 'Content-Type: application/json' -d '{}'
```

### Option 2: Just `docker cp` (works right now, no container changes)

Your command in the notes is correct and works immediately:
```bash
docker cp weaviate_local:/var/lib/weaviate data/weaviate-backup-$(date +%Y%m%d)
```

**For now, `docker cp` is the pragmatic choice** since it works with your current container as-is. When you recreate the container with a volume mount, add `ENABLE_MODULES=backup-filesystem` at the same time to get the built-in backup API too.



Weaviate uses a WAL (Write-Ahead Log) and flushes data to disk periodically. With a live `docker cp`, you risk copying mid-flush, which could give you an inconsistent snapshot.

The safest approach is to **stop Weaviate briefly** before copying:

```bash
docker stop weaviate_local
docker cp weaviate_local:/var/lib/weaviate data/weaviate-backup-$(date +%Y%m%d)
docker start weaviate_local
```

`docker stop` sends SIGTERM, which Weaviate handles gracefully — it flushes all pending writes before shutting down. The copy you already took is *probably* fine since Weaviate was likely idle (no active writes), but if you want a guaranteed-clean backup, re-do it with the stop/start sandwich.
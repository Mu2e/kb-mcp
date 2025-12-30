# NERSC HOW TOs

## Parse Documents with Marker on GPU nodes
These instructions are inteneed to re-parse all documents (that we already added with `kb-import`) with Marker parser. The marker parse needs GPUs (otherwise its painfully slow).

Log in and run the default setup to make sure we have the database running.
```bash
source nersc_setup.sh
```

Request an interactive node:
```bash
salloc --nodes 1 --qos interactive --time 01:00:00 --constraint gpu --account m5115_g
```

On the node, default setup and activate special marker (can not be part of the `.venv` which is read only). After that run on all 4 GPUs in parallel:
```bash
cd 
source nersc_setup.sh
. ./scripts/nersc_setup_marker.sh
./scripts/run_on_4gpus.sh kb tools parse-all inspire-hep --extract-images --describe-images --parser-name marker
```

## Extract graph relations 

Start with the default setup, this will also start the DB if its not yet running. Make sure this session stays open
```bash
source nersc_setup.sh
```

Start the vLLM, this takes 5 to 10min to start up. The job duration defaults to 1h.
```bash
./scripts/nersc_launch_llm.sh
```

And run `n` jobs in parallel:
```

```
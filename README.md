# STAGE — One User, One Agent (Submission Repository)

All deliverables, source code, tests, data pack, and sample_run/ outputs are inside ./submission/.

## One-Command Replay

    cd submission && python3 -m src.cli --pack . --out sample_run

## Run Automated Tests

    cd submission && python3 -m unittest discover -s tests -v

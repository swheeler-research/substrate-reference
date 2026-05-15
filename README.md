# Sovereign Substrate: Reference Implementation

A working implementation of the Sovereign Substrate architecture. The architecture is specified in the v1.4 paper (in the related repository / project knowledge); this repository tests whether the architectural commitments are operationally realisable, by building software that honours them.

## Status

Implementation in progress. See [docs/state.md](docs/state.md) for the live snapshot, [docs/handover.md](docs/handover.md) for the full project context, [docs/specification_gaps.md](docs/specification_gaps.md) for the architectural-decisions log, and [docs/phase_1_plan.md](docs/phase_1_plan.md) for the original Phase 1 plan.

## What this is

A reference prototype. The point is to verify or falsify the architectural commitments by running code. Not a production substrate. Not a complete protocol specification. The smallest thing that exercises the central architectural commitments end-to-end.

## What this is not

- Production infrastructure
- A complete protocol specification
- A polished open-source project
- A demonstration of the substrate's full scope (federation across operators, multi-custodian quorum, cross-substrate composition, full cryptographic stack)

## Layout

```
src/substrate/   The substrate library itself
tests/           Tests exercising the architectural commitments
examples/        Worked examples (Universal Credit first)
docs/            Documentation that follows the code
```

## Running

Python 3.11+. No external dependencies for Phase 1 beyond the standard library and `pytest` for tests.

```
pip install -r requirements.txt
python -m pytest tests/
```

## License

All rights reserved. To be reconsidered as the work matures.

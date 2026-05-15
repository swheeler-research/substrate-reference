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
```

## Running

Python 3.11+. No external dependencies for Phase 1 beyond the standard library and `pytest` for tests.

```
pip install -r requirements.txt
python -m pytest tests/
```

## License

All rights reserved. To be reconsidered as the work matures.

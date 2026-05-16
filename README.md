# The Sovereign Substrate: Reference Implementation

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20238427.svg)](https://doi.org/10.5281/zenodo.20238427)
[![Licence: CC BY 4.0](https://img.shields.io/badge/Licence-CC%20BY%204.0-blue.svg)](https://creativecommons.org/licenses/by/4.0/)

Python reference implementation of the Sovereign Substrate architecture.

## Scope

This implementation is a **reference**, not a production substrate. The architectural mechanisms are substantiated against end-to-end demonstrations and a test suite, but production conforming implementations must address engineering depth this prototype does not reach. The companion paper names the architectural commitments specified but not yet substantiated by this prototype, and develops the conformance treatment in full.

The architectural commitments are stable; what differs is the engineering depth at which this prototype expresses them.

## The architecture

The architecture this implementation conforms to is specified in two papers:

- **Principal paper** (architectural treatise): *The Sovereign Substrate: A constitutional architecture for governed computation*, [10.5281/zenodo.19960841](https://doi.org/10.5281/zenodo.19960841)
- **Companion paper** (reference architecture): *The Sovereign Substrate: Reference Architecture and Implementation*, [10.5281/zenodo.20237900](https://doi.org/10.5281/zenodo.20237900)

The companion paper specifies the architecture at the depth required to write a conforming implementation. This implementation is what that paper documents. Read the companion paper before the code.

## Quickstart

```bash
git clone https://github.com/swheeler-research/substrate-reference.git
cd substrate-reference
```

Install dependencies per the project's package metadata. Run the test suite with `pytest`. End-to-end demonstrations are in `examples/`.

The companion paper walks through selected demonstrations in detail at mechanism-coverage depth. The remaining demonstrations are described in the principal paper's case treatments and in the per-example documentation under `examples/`.

## Citation

> Wheeler, S. *The Sovereign Substrate: Reference Implementation*. Zenodo. <https://doi.org/10.5281/zenodo.20238427>

BibTeX, RIS, CSL, and CFF exports are available on the [Zenodo record](https://doi.org/10.5281/zenodo.20238427). The repository's `CITATION.cff` carries the same metadata in machine-readable form.

When referencing the substrate as a whole, the architectural papers should be cited alongside the implementation:

> Wheeler, S. *The Sovereign Substrate: A constitutional architecture for governed computation*. Zenodo. <https://doi.org/10.5281/zenodo.19960841>

> Wheeler, S. *The Sovereign Substrate: Reference Architecture and Implementation*. Zenodo. <https://doi.org/10.5281/zenodo.20237900>

## Licence

Licensed under the [Creative Commons Attribution 4.0 International licence](https://creativecommons.org/licenses/by/4.0/) (CC BY 4.0). Copy, redistribution, adaptation, and derivative work in any medium or format, including commercial use, on the single condition that the author and the work are attributed.

## Correspondence

S. Wheeler, independent researcher. ORCID: [0009-0009-8693-0148](https://orcid.org/0009-0009-8693-0148). Email: swheeler-research@proton.me.
## What changed

<!-- One paragraph. Link the issue. -->

## Why

<!-- The problem, the design constraint, and why this is the smallest correct change. -->

## How it was verified

- [ ] `make model-test`
- [ ] `make sw-test`
- [ ] `make rtl-lint`
- [ ] `make rtl-test`
- [ ] `make formal` (or explain why not)
- [ ] `make synth-check`
- [ ] `make spdx-check`
- [ ] `make lock-check` (if PPA/UI data changed)

## Release impact

- [ ] Regenerated `model/results.md` / `model/dse.md` (if the model moved)
- [ ] New `flow/records/` entry and `flow/results.md` (if PPA moved)
- [ ] Updated `docs/specification.md` (if the contract changed)
- [ ] Updated `verif/README.md` case counts (if a configuration changed)
- [ ] No new SPDX/header gaps

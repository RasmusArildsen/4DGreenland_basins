# Contributing

Thank you for helping improve the Greenland hydrological drainage-basin workflow.

1. Open an issue describing the scientific or technical change.
2. Create a focused branch from `main`.
3. Add or update tests for changed behaviour.
4. Run `python -m pytest` and `python -m ruff check .`.
5. Open a pull request explaining the change, its scientific implications, and any changed outputs.

Do not commit input data, generated GeoTIFFs, GRASS databases, or cluster logs. Document input-product
versions, coordinate reference systems, resampling methods, and random seeds whenever they affect
reproducibility.

For suspected errors in a released scientific product, include the target (`surf`, `bed`, or `hybrid`),
resolution, ensemble configuration, and the smallest reproducible example available.

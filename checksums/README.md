# Checksums

`SHA256SUMS` and `MODEL_SHA256SUMS` are generated files for a complete offline
bundle. They are intentionally not committed to the source-only public
repository because the referenced container images, wheels, source snapshots,
and model weights are not distributed through GitHub.

After preparing all offline artifacts, generate the bundle manifest with:

```bash
./scripts/update_checksums.sh
```

Generate the optional model-weight manifest separately with:

```bash
./scripts/generate_model_sha256.sh
```

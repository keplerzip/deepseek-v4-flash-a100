# R2 → R2.3 offline incremental update

This delivery requires the exact R2 image ID recorded in `base.env`. It builds
one small Docker overlay locally with `--network none --pull=false`; no package
download, CUDA compilation, Python installation or target mutation outside
Docker is performed.

```bash
./update-from-r2.sh one   # build the overlay, then start target
./update-from-r2.sh two   # build the overlay, then start DSpark k=7
```

The installer verifies the base image ID and source revision, every payload
file, the resulting installed-file hashes, vLLM package metadata and the
DeepSeek V4 backend import contract. If the old image is absent or differs, it
stops and requires the full R2.3 package.

# Fixed-base source snapshot

`vllm-1281004-source.tar.gz` is produced directly from the locked haosdent
commit `12810046c799cbe874967e19b1c0fa134ab7b209`. The complete offline delivery
retains it for source audit, reproduction, and installed-tree hash
verification. The public GitHub source-only checkout intentionally omits the
archive while retaining its checksum and source lock.

The final target package does not build from this archive. The build host uses
the exact Git commit and four CPU jobs, verifies the resulting image, and ships
it in `r1/images/dsv4-a100-r1-images.tar`. Target deployment only performs a
checksum-verified `docker image load` and fails before loading if a fixed tag
already points at another image ID.

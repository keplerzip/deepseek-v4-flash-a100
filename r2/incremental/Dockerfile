ARG BASE_IMAGE
FROM ${BASE_IMAGE}

# The full R2.1 wheel differs from R2 in exactly two installed vLLM files.
# Replace those files and its small dist-info directory without downloading or
# compiling anything on the target machine.
RUN python3 -c "from pathlib import Path; import shutil; p=Path('/usr/local/lib/python3.12/dist-packages/vllm-0.1.dev40+g9667db18a.d20260826.dist-info'); assert p.is_dir(), p; shutil.rmtree(p)"
COPY overlay/vllm/_version.py /usr/local/lib/python3.12/dist-packages/vllm/_version.py
COPY overlay/vllm/models/deepseek_v4/nvidia/flashinfer_sparse.py /usr/local/lib/python3.12/dist-packages/vllm/models/deepseek_v4/nvidia/flashinfer_sparse.py
COPY overlay/vllm-0.1.dev41+gbc51bfa79.d20260830.dist-info/ /usr/local/lib/python3.12/dist-packages/vllm-0.1.dev41+gbc51bfa79.d20260830.dist-info/

ARG RESULT_SOURCE_COMMIT
ARG RELEASE
ARG BASE_IMAGE_ID
ARG PAYLOAD_MANIFEST_SHA256
LABEL org.opencontainers.image.revision="${RESULT_SOURCE_COMMIT}" \
      com.deepseek.release="${RELEASE}" \
      com.deepseek.cuda.arch="8.0" \
      com.deepseek.build.max-jobs="8" \
      com.deepseek.build.nvcc-threads="1" \
      com.deepseek.incremental.base-image-id="${BASE_IMAGE_ID}" \
      com.deepseek.incremental.payload-manifest-sha256="${PAYLOAD_MANIFEST_SHA256}"

#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import re
import unittest
from pathlib import Path

R2 = Path(__file__).resolve().parents[1]
ROOT = R2.parent


class PackageContractTest(unittest.TestCase):
    @staticmethod
    def load_module(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"cannot import {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def env_value(path: Path, key: str) -> str:
        prefix = f"{key}="
        values = [
            line.removeprefix(prefix)
            for line in path.read_text().splitlines()
            if line.startswith(prefix)
        ]
        if len(values) != 1:
            raise AssertionError(f"expected one exact {key}= assignment in {path}")
        return values[0]

    def test_locked_runtime_contract(self):
        common_path = R2 / "config/common.env"
        release_path = R2 / "config/release.env"
        expected_release = {
            "R2_RELEASE": "2026.08.30-r2.1",
            "MAX_MODEL_LEN": "1048576",
            "SHORT_MODEL_MAX_LEN": "262144",
            "MAX_NUM_SEQS": "16",
            "MAX_NUM_BATCHED_TOKENS": "4096",
            "GPU_DEVICES": "0,1,2,3,4,5,6,7",
            "GPU_COUNT": "8",
            "TENSOR_PARALLEL_SIZE": "8",
            "HOST": "0.0.0.0",
            "PORT": "8005",
            "HOST_PUBLISH_ADDRESS": "127.0.0.1",
            "NETWORK_MODE": "bridge",
        }
        self.assertEqual(
            {key: self.env_value(release_path, key) for key in expected_release},
            expected_release,
        )
        self.assertEqual(
            self.env_value(common_path, "PREFIX_CACHE_PROFILE"),
            "${PREFIX_CACHE_PROFILE:-zero}",
        )
        lib = (R2 / "scripts/lib.sh").read_text()
        self.assertLess(
            lib.index('source "$R2_DIR/config/secrets.env"'),
            lib.index('source "$R2_DIR/config/release.env"'),
        )
        self.assertIn("readonly MAX_NUM_BATCHED_TOKENS KV_CACHE_DTYPE BLOCK_SIZE", lib)
        self.assertIn("assert_release_contract", lib)
        source_lock = json.loads((R2 / "manifests/source-lock.json").read_text())
        self.assertEqual(source_lock["build"]["max_jobs"], 8)
        self.assertEqual(source_lock["build"]["nvcc_threads"], 1)
        self.assertTrue(source_lock["build"]["compiled_on_build_host"])
        self.assertFalse(source_lock["build"]["target_host_compilation"])
        validation = json.loads(
            (R2 / "manifests/build-validation.json").read_text()
        )
        self.assertEqual(validation["image"]["build_max_jobs"], 8)
        self.assertEqual(validation["image"]["build_nvcc_threads"], 1)
        self.assertEqual(validation["image"]["cuda_arch"], "8.0")
        self.assertEqual(
            validation["image"]["source_revision"],
            source_lock["r2_source_commit"],
        )
        self.assertGreater(
            validation["runtime"]["native_extension_sm80_cubin_entries"], 0
        )
        build_script = (R2 / "scripts/build_image.sh").read_text()
        self.assertIn("com.deepseek.build.max-jobs", build_script)
        self.assertIn("com.deepseek.build.nvcc-threads", build_script)
        package_script = (R2 / "scripts/package_offline_release.sh").read_text()
        self.assertIn("precompiled image MAX_JOBS provenance mismatch", package_script)
        self.assertIn(
            "precompiled image NVCC_THREADS provenance mismatch", package_script
        )
        self.assertIn("precompiled image CUDA architecture mismatch", package_script)
        load_script = (R2 / "scripts/load_image.sh").read_text()
        self.assertIn("loaded image CUDA architecture provenance mismatch", load_script)
        preflight = (R2 / "scripts/preflight.sh").read_text()
        self.assertIn("image CUDA architecture provenance must be SM80/8.0", preflight)
        self.assertIn("from vllm.models.deepseek_v4 import", preflight)
        for script in ("build_image.sh", "load_image.sh", "run_package_tests.sh"):
            self.assertIn(
                "verify_runtime_source.py", (R2 / f"scripts/{script}").read_text()
            )

    def test_four_exact_aliases_and_limits(self):
        lib = (R2 / "scripts/lib.sh").read_text()
        expected = (
            "deepseek-v4-flash",
            "deepseek-v4-flash[1M]",
            "deepseek-v4-flash-claude",
            "deepseek-v4-flash-claude[1M]",
        )
        array_match = re.search(
            r"declare -ar SERVED_MODEL_NAMES=\((?P<body>.*?)\n\)", lib, re.DOTALL
        )
        self.assertIsNotNone(array_match)
        aliases = tuple(re.findall(r"^\s*'([^']+)'\s*$", array_match["body"], re.MULTILINE))
        self.assertEqual(aliases, expected)
        mapping_line = next(
            line
            for line in (R2 / "config/release.env").read_text().splitlines()
            if line.startswith("SERVED_MODEL_MAX_LENS=")
        )
        mapping = json.loads(mapping_line.split("=", 1)[1].strip("'"))
        self.assertEqual(
            mapping,
            {
                "deepseek-v4-flash": 262_144,
                "deepseek-v4-flash[1M]": 1_048_576,
                "deepseek-v4-flash-claude": 262_144,
                "deepseek-v4-flash-claude[1M]": 1_048_576,
            },
        )

    def test_two_schemes_are_tp8_c16_and_dspark_defaults_k7(self):
        release_path = R2 / "config/release.env"
        target = (R2 / "config/schemes/target.env").read_text()
        dspark = (R2 / "config/schemes/dspark.env").read_text()
        self.assertEqual(self.env_value(release_path, "TENSOR_PARALLEL_SIZE"), "8")
        self.assertEqual(self.env_value(release_path, "MAX_NUM_SEQS"), "16")
        self.assertIn("SPECULATIVE_METHOD=none", target)
        self.assertIn("DSPARK_K=${DSV4_DSPARK_K:-7}", dspark)
        start = (R2 / "scripts/start.sh").read_text()
        self.assertIn('"method":"dspark"', start)
        self.assertIn('"draft_sample_method":"greedy"', start)

    def test_network_is_docker_internal_not_lan(self):
        start = (R2 / "scripts/start.sh").read_text()
        self.assertIn('--publish "$HOST_PUBLISH_ADDRESS:$PORT:$PORT"', start)
        self.assertIn('--publish "$docker_bridge_gateway:$PORT:$PORT"', start)
        self.assertNotIn('--publish "0.0.0.0:$PORT:$PORT"', start)
        self.assertNotIn("--network host", start)

    def test_benchmark_axes_and_cache_gate(self):
        path = R2 / "benchmarks/long_context_matrix.py"
        module = self.load_module("matrix", path)
        self.assertEqual(
            module.DEFAULT_CONTEXTS,
            (200_000, 400_000, 600_000, 800_000, 1_000_000),
        )
        self.assertEqual(module.DEFAULT_OUTPUTS, (10_000, 20_000, 30_000))
        self.assertEqual(module.DEFAULT_HIT_RATES, (0.80, 0.85, 0.90, 0.95))
        source = path.read_text()
        self.assertIn('default=16', source)
        self.assertIn('default=0.01', source)
        self.assertIn("中文注释", source)

    def test_prometheus_counter_total_suffix_is_measured(self):
        module = self.load_module(
            "matrix_metrics", R2 / "benchmarks/long_context_matrix.py"
        )
        metrics = module.parse_metric_totals(
            "\n".join(
                (
                    'vllm:prefix_cache_queries_total{engine=\"0\"} 100',
                    'vllm:prefix_cache_hits_total{engine=\"0\"} 92',
                    'vllm:spec_decode_num_draft_tokens_total{engine=\"0\"} 70',
                    'vllm:spec_decode_num_accepted_tokens_total{engine=\"0\"} 49',
                    'vllm:spec_decode_num_draft_tokens_created{engine=\"0\"} 1',
                )
            )
        )
        self.assertEqual(metrics["vllm:prefix_cache_queries"], 100)
        self.assertEqual(metrics["vllm:prefix_cache_hits"], 92)
        self.assertEqual(metrics["vllm:spec_decode_num_draft_tokens"], 70)
        self.assertEqual(metrics["vllm:spec_decode_num_accepted_tokens"], 49)

    def test_aggregate_decode_excludes_prefill_window(self):
        module = self.load_module(
            "matrix_decode", R2 / "benchmarks/long_context_matrix.py"
        )
        results = [
            {
                "first_token_at_perf": 10.0,
                "finished_at_perf": 20.0,
                "completion_tokens": 101,
            },
            {
                "first_token_at_perf": 12.0,
                "finished_at_perf": 22.0,
                "completion_tokens": 101,
            },
        ]
        self.assertAlmostEqual(module.aggregate_decode_tps(results), 200 / 12)

    def test_cache_regression_gate_is_paired_per_cell(self):
        module = self.load_module(
            "cache_selector", R2 / "benchmarks/select_cache_profile.py"
        )
        baseline = {(200_000, 256, 0.9): 0.91, (1_000_000, 256, 0.9): 0.95}
        candidate = {(200_000, 256, 0.9): 0.93, (1_000_000, 256, 0.9): 0.93}
        self.assertAlmostEqual(
            module.worst_paired_delta_pp(baseline, candidate), -2.0
        )
        self.assertIsNone(
            module.worst_paired_delta_pp(
                baseline, {(200_000, 256, 0.9): 0.93}
            )
        )

    def test_k_sweep_is_exact(self):
        sweep = (R2 / "scripts/run_dspark_k_sweep.sh").read_text()
        self.assertIn("for k in 1 3 5 7", sweep)
        self.assertIn("--concurrency 16", sweep)

    def test_target_does_not_receive_speculative_config(self):
        start = (R2 / "scripts/start.sh").read_text()
        self.assertIn('if [[ "$SCHEME_ID" == dspark ]]', start)
        self.assertIn("target safety invariant", start)

    def test_incremental_update_is_pinned_and_offline(self):
        base_path = R2 / "incremental/base.env"
        expected = {
            "INCREMENTAL_BASE_IMAGE": "dsv4-a100:20260826-r2-sm80",
            "INCREMENTAL_BASE_IMAGE_ID": (
                "sha256:5d420df326cf1455ee84ebe988a1c056823f9f800c61bb21eec04d3c4510bfd8"
            ),
            "INCREMENTAL_RESULT_IMAGE": "dsv4-a100:20260830-r2.1-sm80",
            "INCREMENTAL_RESULT_SOURCE_COMMIT": (
                "bc51bfa7903de8cb94144fbab0aac1e6b333e6b6"
            ),
        }
        self.assertEqual(
            {key: self.env_value(base_path, key) for key in expected}, expected
        )
        installer = (R2 / "incremental/install.sh").read_text()
        self.assertIn("--network none", installer)
        self.assertIn("--pull=false", installer)
        self.assertIn('"$observed_base_id" == "$INCREMENTAL_BASE_IMAGE_ID"', installer)
        self.assertNotIn("pip install", installer)
        dockerfile = (R2 / "incremental/Dockerfile").read_text()
        self.assertNotIn("apt-get", dockerfile)
        self.assertNotIn("pip install", dockerfile)
        packager = (R2 / "scripts/package_incremental_release.sh").read_text()
        self.assertNotIn("image save", packager)

    def test_required_entrypoints_exist(self):
        for name in (
            "start_one.sh",
            "start_two.sh",
            "benchmark_one.sh",
            "benchmark_two.sh",
            "benchmark_cache_profiles.sh",
            "benchmark_dspark_k.sh",
            "report_one.sh",
            "report_two.sh",
            "run-tests.sh",
            "update-from-r2.sh",
        ):
            self.assertTrue((ROOT / name).is_file(), name)


if __name__ == "__main__":
    unittest.main(verbosity=2)

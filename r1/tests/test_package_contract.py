#!/usr/bin/env python3
"""Static release checks that do not require a model or GPU."""

from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R1 = ROOT / "r1"


def load_benchmark_module():
    path = R1 / "benchmarks/performance_matrix.py"
    spec = importlib.util.spec_from_file_location("performance_matrix", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_tool_matrix_module():
    path = R1 / "tests/tool_matrix_test.py"
    spec = importlib.util.spec_from_file_location("tool_matrix_test", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_report_packager_module():
    path = R1 / "reports/package_portable_report.py"
    spec = importlib.util.spec_from_file_location("package_portable_report", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_report_generator_module():
    path = R1 / "reports/generate_artifact.py"
    spec = importlib.util.spec_from_file_location("generate_artifact", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_env(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        result[key] = value
    return result


class PackageContractTests(unittest.TestCase):
    def test_fixed_runtime_contract(self):
        values = parse_env(R1 / "config/target.env")
        self.assertEqual(values["BASE_IMAGE"], "dsv4-a100:1281004-base")
        self.assertEqual(values["R1_IMAGE"], "dsv4-a100:1281004-r1-20260820")
        self.assertEqual(
            values["SOURCE_TEST_IMAGE"],
            "dsv4-a100:1281004-r1-tests-20260820",
        )
        self.assertEqual(values["MAX_MODEL_LEN"], "262144")
        self.assertEqual(values["MIN_NVIDIA_DRIVER"], "580.126.20")
        self.assertEqual(values["HOST"], "${HOST:-0.0.0.0}")
        self.assertEqual(values["API_PROBE_HOST"], "127.0.0.1")
        self.assertEqual(values["HOST_PUBLISH_ADDRESS"], "127.0.0.1")
        self.assertEqual(values["NETWORK_MODE"], "bridge")
        self.assertEqual(values["PORT"], "${PORT:-8005}")
        one = parse_env(R1 / "config/schemes/one.env")
        two = parse_env(R1 / "config/schemes/two.env")
        self.assertEqual(one["GPU_DEVICES"], "0,1,2,3,4,5,6,7")
        self.assertEqual(one["TENSOR_PARALLEL_SIZE"], "8")
        self.assertEqual(one["MAX_NUM_SEQS"], "32")
        self.assertEqual(one["BENCHMARK_MAX_CONCURRENCY"], "16")
        self.assertEqual(two["GPU_DEVICES"], "4,5,6,7")
        self.assertEqual(two["TENSOR_PARALLEL_SIZE"], "4")
        self.assertEqual(two["MAX_NUM_SEQS"], "16")
        self.assertEqual(two["BENCHMARK_MAX_CONCURRENCY"], "8")
        self.assertEqual(two["VLLM_SPARSE_DENSE_QUERY_BLOCK"], "4")
        preflight = (R1 / "scripts/preflight.sh").read_text()
        self.assertIn("name,memory.total,driver_version", preflight)
        self.assertIn('sort -V', preflight)

    def test_benchmark_contract_has_320_and_160_cell_schemes(self):
        benchmark = load_benchmark_module()
        self.assertEqual(benchmark.CONCURRENCIES, tuple(range(1, 17)))
        self.assertEqual(benchmark.CONTEXTS, tuple(range(10_000, 200_001, 10_000)))
        self.assertEqual(len(benchmark.CONCURRENCIES) * len(benchmark.CONTEXTS), 320)
        contract = json.loads((R1 / "manifests/benchmark-contract.json").read_text())
        self.assertEqual(contract["schemes"]["one"]["matrix_cells"], 320)
        self.assertEqual(contract["schemes"]["two"]["matrix_cells"], 160)
        self.assertEqual(
            contract["schemes"]["two"]["theoretical_full_256k_concurrency"],
            11.8,
        )

    def test_live_tool_matrix_is_complete(self):
        matrix = load_tool_matrix_module()
        plan = matrix.plan()
        self.assertEqual(plan["method"], "target")
        self.assertEqual(plan["cases"], 36)
        self.assertEqual(plan["requests"], 156)
        self.assertEqual(plan["concurrencies"], [1, 4, 8])
        self.assertEqual(plan["tool_choices"], ["auto", "required", "none"])
        self.assertEqual(plan["streams"], [False, True])
        self.assertEqual(plan["thinking"], [False, True])
        self.assertEqual(
            set(plan["parameter_types"]),
            {"string", "boolean", "integer", "number", "array", "object"},
        )
        self.assertTrue(plan["multiline_heredoc"])

    def test_exact_leak_markers_are_blocked(self):
        contract = (R1 / "tests/api_contract_test.py").read_text()
        performance = (R1 / "benchmarks/performance_matrix.py").read_text()
        for marker in (
            "<｜DSML｜",
            "<| DSML|",
            "<invoke",
            "<parameter",
            "</invoke>",
        ):
            self.assertIn(marker, contract)
            self.assertIn(marker, performance)

    def test_parser_safety_contract_maps_to_real_tests(self):
        contract = json.loads(
            (R1 / "manifests/parser-safety-contract.json").read_text()
        )
        test_source = (ROOT / contract["test_file"]).read_text()
        requirement_ids = {item["id"] for item in contract["requirements"]}
        self.assertEqual(
            requirement_ids,
            {
                "missing_tool_calls_wrapper",
                "toolcalls_typo_recovery",
                "declared_tools_only",
                "commit_only_after_complete_invoke",
                "truncation_rolls_back_to_text",
                "tool_choice_none_never_recovers",
                "suffix_content_preserved",
                "parallel_and_consecutive_calls",
                "streaming_nonstreaming_parity",
                "argument_types_and_multiline",
            },
        )
        for requirement in contract["requirements"]:
            self.assertTrue(requirement["tests"], requirement["id"])
            for test_name in requirement["tests"]:
                self.assertIn(f"def {test_name}(", test_source, test_name)

    def test_initial_report_data_has_every_pending_cell(self):
        generator = load_report_generator_module()
        schemes = (
            ("one", "performance-matrix.csv", 16, 320),
            ("two", "performance-matrix-two.csv", 8, 160),
        )
        for scheme, filename, max_concurrency, cells in schemes:
            path = R1 / "reports/data" / filename
            with path.open(newline="") as handle:
                scheme_rows = list(csv.DictReader(handle))
            self.assertEqual(len(scheme_rows), cells)
            self.assertEqual({row["status"] for row in scheme_rows}, {"pending"})
            self.assertEqual(
                {int(row["concurrency"]) for row in scheme_rows},
                set(range(1, max_concurrency + 1)),
            )
            self.assertEqual(
                {int(row["context_target"]) for row in scheme_rows},
                set(range(10_000, 200_001, 10_000)),
            )
            loaded = generator.load_rows(path, max_concurrency)
            payload = generator.artifact(
                loaded,
                f"r1/reports/data/{filename}",
                scheme=scheme,
                benchmark_max_concurrency=max_concurrency,
            )
            summary = payload["snapshot"]["datasets"]["summary"][0]
            self.assertEqual(summary["planned_cells"], cells)

        path = R1 / "reports/data/performance-matrix.csv"
        with path.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        pending_rows = generator.load_rows(path)
        self.assertEqual(len(pending_rows), 320)
        pending_payload = generator.artifact(
            pending_rows, "r1/reports/data/performance-matrix.csv"
        )
        self.assertTrue(pending_payload["ok"])
        self.assertEqual(pending_payload["widget_type"], "artifact")
        self.assertTrue(pending_payload["package_info"]["hostedReadOnly"])
        self.assertEqual(
            pending_payload["package_info"], pending_payload["packageInfo"]
        )
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / "malformed.csv"
            copied = [dict(row) for row in rows]
            copied[0]["status"] = "complete"
            with malformed.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=copied[0])
                writer.writeheader()
                writer.writerows(copied)
            with self.assertRaisesRegex(RuntimeError, "omitted metrics"):
                generator.load_rows(malformed)

            completed = Path(directory) / "completed.csv"
            for row in copied:
                row["status"] = "complete"
                row["requests_success"] = row["requests_planned"]
                row["requests_failed"] = "0"
                row["prompt_tokens_min"] = row["context_target"]
                row["prompt_tokens_p50"] = row["context_target"]
                row["prompt_tokens_max"] = row["context_target"]
                row["completion_tokens_total"] = "1"
                for field in generator.FLOAT_FIELDS:
                    row[field] = "1.0"
            with completed.open("w", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=copied[0])
                writer.writeheader()
                writer.writerows(copied)
            completed_payload = generator.artifact(
                generator.load_rows(completed), "synthetic-browser-qa.csv"
            )
            self.assertEqual(completed_payload["snapshot"]["status"], "ready")
            chart_ids = {
                chart["id"] for chart in completed_payload["manifest"]["charts"]
            }
            self.assertTrue({"ttft-heatmap", "throughput-heatmap"}.issubset(chart_ids))
            block_ids = {
                block["id"] for block in completed_payload["manifest"]["blocks"]
            }
            self.assertIn("matrix-table-block", block_ids)

            two_path = R1 / "reports/data/performance-matrix-two.csv"
            with two_path.open(newline="") as handle:
                completed_two_rows = list(csv.DictReader(handle))
            for row in completed_two_rows:
                row["status"] = "complete"
                row["requests_success"] = row["requests_planned"]
                row["requests_failed"] = "0"
                row["prompt_tokens_min"] = row["context_target"]
                row["prompt_tokens_p50"] = row["context_target"]
                row["prompt_tokens_max"] = row["context_target"]
                row["completion_tokens_total"] = "1"
                for field in generator.FLOAT_FIELDS:
                    row[field] = "1.0"
            completed_two = Path(directory) / "completed-two.csv"
            with completed_two.open("w", newline="") as handle:
                writer = csv.DictWriter(
                    handle, fieldnames=completed_two_rows[0]
                )
                writer.writeheader()
                writer.writerows(completed_two_rows)
            completed_two_payload = generator.artifact(
                generator.load_rows(completed_two, 8),
                "synthetic-browser-qa-two.csv",
                scheme="two",
                benchmark_max_concurrency=8,
            )
            self.assertEqual(completed_two_payload["snapshot"]["status"], "ready")
            ttft_chart = next(
                chart
                for chart in completed_two_payload["manifest"]["charts"]
                if chart["id"] == "ttft-heatmap"
            )
            self.assertEqual(
                ttft_chart["encodings"]["y"]["fields"],
                [f"c{value}" for value in range(1, 9)],
            )

    def test_portable_report_delivery_receipt(self):
        receipt = json.loads((R1 / "reports/qa/delivery-receipt.json").read_text())
        self.assertTrue(receipt["ok"])
        self.assertEqual(receipt["viewports"], [1440, 390])
        self.assertEqual(receipt["counts"]["charts"], 1)
        self.assertEqual(receipt["counts"]["metrics"], 7)
        self.assertEqual(receipt["counts"]["tables"], 1)
        report = (R1 / "reports/performance-report.html").read_text()
        self.assertIn("data-data-analytics-portable-artifact", report)
        self.assertIn("data-analytics-portable-artifact-payload-source", report)
        self.assertIn("All 320 benchmark cells", report)
        self.assertIn('data-dsv4-portable-overflow-fix="true"', report)
        report_two = (R1 / "reports/performance-report-two.html").read_text()
        self.assertIn("All 160 benchmark cells", report_two)
        self.assertIn('data-target-runtime-report="true"', report_two)
        receipt_two = json.loads(
            (R1 / "reports/qa/delivery-receipt-two.json").read_text()
        )
        self.assertTrue(receipt_two["ok"])
        self.assertEqual(receipt_two["viewports"], [1440, 390])
        completed_receipt = json.loads(
            (R1 / "reports/qa/completed-layout-receipt.json").read_text()
        )
        self.assertTrue(completed_receipt["ok"])
        self.assertEqual(completed_receipt["counts"]["charts"], 3)
        self.assertEqual(completed_receipt["counts"]["metrics"], 7)
        self.assertEqual(completed_receipt["counts"]["tables"], 1)
        self.assertEqual(completed_receipt["viewports"], [1440, 390])

    def test_target_can_repackage_real_artifact_without_host_node(self):
        packager = load_report_packager_module()
        artifact_path = R1 / "reports/performance-report.artifact.json"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "performance-report.html"
            packager.package_report(
                artifact_path,
                R1 / "reports/performance-report.html",
                output,
            )
            document = output.read_text()
        self.assertIn('data-target-runtime-report="true"', document)
        self.assertIn("All 320 benchmark cells", document)
        match = re.search(
            r'id="data-analytics-portable-artifact-payload-source"[^>]*>\s*'
            r"(.*?)\s*</template>",
            document,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        decoded = gzip.decompress(base64.b64decode(match.group(1))).decode()
        self.assertEqual(json.loads(decoded), json.loads(artifact_path.read_text()))
        generator = (R1 / "scripts/generate_result_artifact.sh").read_text()
        self.assertIn("package_portable_report.py", generator)
        self.assertIn('data-dsv4-portable-overflow-fix="true"', document)
        self.assertEqual(document.count('data-dsv4-portable-overflow-fix="true"'), 1)
        artifact_two = R1 / "reports/performance-report-two.artifact.json"
        with tempfile.TemporaryDirectory() as directory:
            output_two = Path(directory) / "performance-report-two.html"
            packager.package_report(
                artifact_two,
                R1 / "reports/performance-report.html",
                output_two,
            )
            self.assertIn("All 160 benchmark cells", output_two.read_text())
        workstation_builder = (R1 / "reports/build_report.sh").read_text()
        self.assertIn("build_portable_artifact.mjs", workstation_builder)
        self.assertIn("verify_portable_artifact.mjs", workstation_builder)
        self.assertIn("harden_portable_report.py", workstation_builder)

    def test_bundled_fixed_base_source(self):
        archive = R1 / "base/vllm-1281004-source.tar.gz"
        if not archive.exists():
            public_note = (R1 / "base/README.md").read_text()
            self.assertIn("source-only checkout intentionally omits", public_note)
            self.skipTest("public source-only checkout omits the fixed base archive")
        checksum = (R1 / "base/vllm-1281004-source.tar.gz.sha256").read_text()
        expected = checksum.split()[0]
        digest = hashlib.sha256()
        with archive.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        self.assertEqual(digest.hexdigest(), expected)
        with tarfile.open(archive, "r:gz") as bundle:
            names = set(bundle.getnames())
            for line in (R1 / "manifests/base-python.sha256").read_text().splitlines():
                expected_file_hash, relative = line.split("  ", 1)
                member = bundle.extractfile(f"vllm-1281004/{relative}")
                self.assertIsNotNone(member, relative)
                observed_file_hash = hashlib.sha256(member.read()).hexdigest()
                self.assertEqual(observed_file_hash, expected_file_hash, relative)
        self.assertIn("vllm-1281004/docker/Dockerfile", names)
        self.assertIn("vllm-1281004/vllm/parser/deepseek_v4.py", names)

    def test_offline_source_test_wheelhouse(self):
        wheelhouse = R1 / "test-wheelhouse"
        entries = (wheelhouse / "sha256sums.txt").read_text().splitlines()
        self.assertEqual(len(entries), 8)
        if not all((wheelhouse / line.split("  ", 1)[1]).exists() for line in entries):
            public_note = (wheelhouse / "README.md").read_text()
            self.assertIn("source-only GitHub repository", public_note)
            self.skipTest("public source-only checkout omits the test wheels")
        names = set()
        for line in entries:
            expected, name = line.split("  ", 1)
            names.add(name)
            self.assertEqual(
                hashlib.sha256((wheelhouse / name).read_bytes()).hexdigest(),
                expected,
            )
        self.assertIn("pytest-9.1.1-py3-none-any.whl", names)
        self.assertIn("tblib-3.1.0-py3-none-any.whl", names)
        self.assertIn("pytest_asyncio-1.4.0-py3-none-any.whl", names)
        dockerfile = (R1 / "docker/Dockerfile.source-tests").read_text()
        self.assertIn("--no-index", dockerfile)
        self.assertIn("sha256sum -c sha256sums.txt", dockerfile)

    def test_target_launch_has_no_speculative_configuration(self):
        script = (R1 / "scripts/start.sh").read_text()
        start = script.index("vllm_args=(")
        end = script.index("\n)", start)
        launch_arguments = script[start:end]
        self.assertNotIn("speculative", launch_arguments.lower())
        self.assertNotIn("dspark", launch_arguments.lower())
        self.assertIn('--max-num-seqs "$MAX_NUM_SEQS"', launch_arguments)
        self.assertIn(
            '--env "VLLM_SPARSE_DENSE_QUERY_BLOCK=${VLLM_SPARSE_DENSE_QUERY_BLOCK:-8}"',
            script,
        )
        self.assertIn("<redacted>", script)
        rollback = (R1 / "scripts/rollback.sh").read_text()
        self.assertLess(rollback.index("preflight.sh"), rollback.index("stop.sh"))
        self.assertIn("--base --skip-port", rollback)

    def test_scheme_switch_is_owned_locked_and_non_destructive(self):
        start = (R1 / "scripts/start.sh").read_text()
        self.assertLess(
            start.index("ALTERNATE_CONTAINER_NAME"),
            start.index('"$R1_DIR/scripts/preflight.sh"'),
        )
        self.assertIn('assert_owned_container "$alternate"', start)
        self.assertIn('docker_cmd stop --time 120 "$alternate"', start)
        self.assertNotIn('container rm "$alternate"', start)
        self.assertIn('$CONTROL_DIR/start-stop.lock', start)
        stop = (R1 / "scripts/stop.sh").read_text()
        self.assertIn("SCHEME_ONE_CONTAINER_NAME", stop)
        self.assertIn("SCHEME_TWO_CONTAINER_NAME", stop)
        self.assertIn('$CONTROL_DIR/start-stop.lock', stop)

    def test_api_is_published_to_docker_only_not_lan(self):
        contract = json.loads(
            (R1 / "manifests/deployment-contract.json").read_text()
        )
        network = contract["api_network"]
        self.assertEqual(network["scope"], "host-and-docker-internal")
        self.assertFalse(network["lan_exposed"])
        self.assertEqual(network["health_path"], "/v1/models")
        self.assertEqual(
            network["docker_base_url"],
            "http://host.docker.internal:8005/v1",
        )
        start = (R1 / "scripts/start.sh").read_text()
        self.assertIn('--network "$NETWORK_MODE"', start)
        self.assertNotIn("--network host", start)
        self.assertIn(
            '--publish "$HOST_PUBLISH_ADDRESS:$PORT:$PORT"', start
        )
        self.assertIn(
            '--publish "$docker_bridge_gateway:$PORT:$PORT"', start
        )
        self.assertIn('docker_cmd network inspect "$NETWORK_MODE"', start)
        self.assertIn("network={{.HostConfig.NetworkMode}}", start)
        self.assertIn("ports={{json .HostConfig.PortBindings}}", start)
        status = (R1 / "scripts/status.sh").read_text()
        self.assertIn("host.docker.internal", status)
        self.assertIn('docker_cmd port "$container" "$PORT/tcp"', status)

    def test_archive_hotfix_is_guarded_and_restart_is_explicit(self):
        hotfix = (ROOT / "hotfix_archive_20260821.sh").read_text()
        self.assertIn("hotfix_id=2026.08.21-hf1", hotfix)
        self.assertIn("r1/hotfix-backups/$timestamp", hotfix)
        self.assertIn("not the dual-scheme offline package", hotfix)
        self.assertIn('if [[ -n "$restart_scheme" ]]', hotfix)
        self.assertIn('"$root_dir/stop.sh"', hotfix)
        self.assertIn('"$root_dir/start_${restart_scheme}.sh"', hotfix)
        self.assertIn("service was not touched", hotfix)

    def test_target_scripts_use_docker_privilege_adapter(self):
        for path in (R1 / "scripts").glob("*.sh"):
            if path.name == "lib.sh":
                continue
            for line in path.read_text().splitlines():
                stripped = line.lstrip()
                self.assertFalse(
                    stripped.startswith("docker "),
                    f"direct Docker invocation bypasses docker_cmd: {path}:{line}",
                )
                self.assertFalse(
                    stripped.startswith("sudo "),
                    f"direct sudo invocation is forbidden: {path}:{line}",
                )

    def test_target_gates_capture_required_runtime_evidence(self):
        gates = (R1 / "scripts/run_target_gates.sh").read_text()
        self.assertIn("capture_failed_gate", gates)
        self.assertIn("partial evidence", gates)
        self.assertIn('"$R1_DIR/scripts/load_images.sh"', gates)
        self.assertIn('"$R1_DIR/scripts/run_tool_matrix.sh"', gates)
        self.assertIn('"$R1_DIR/scripts/run_stability.sh"', gates)
        self.assertNotIn('"$R1_DIR/scripts/build_source_test_image.sh"', gates)
        self.assertIn('"$R1_DIR/scripts/run_source_tests.sh"', gates)
        stability = (R1 / "scripts/run_stability.sh").read_text()
        self.assertIn("--requests 500", stability)
        self.assertIn('--concurrency "$STABILITY_CONCURRENCY"', stability)
        self.assertIn("finalize_runtime_evidence", stability)
        library = (R1 / "scripts/lib.sh").read_text()
        self.assertIn("finalize_stability_evidence.py", library)
        evidence = (R1 / "scripts/collect_results.sh").read_text()
        self.assertIn("pip freeze --all", evidence)
        self.assertIn("image-digest.txt", evidence)
        source_tests = (R1 / "scripts/run_source_tests.sh").read_text()
        for required_path in (
            "tests/parser/engine/test_deepseek_v4.py",
            "tests/parser/engine",
            "tests/tokenizers_/test_deepseek_v4.py",
            "tests/models/test_deepseek_v4_target_post_load_contract.py",
            "tests/models/test_deepseek_v4_mega_moe.py",
            "tests/entrypoints/anthropic/test_anthropic_messages_conversion.py",
        ):
            self.assertIn(required_path, source_tests)

    def test_offline_delivery_is_load_only_on_target(self):
        loader = (R1 / "scripts/load_images.sh").read_text()
        self.assertIn("dsv4-a100-r1-images.tar", loader)
        self.assertIn("sha256sum", loader)
        self.assertIn("image load", loader)
        self.assertIn("image tag conflict", loader)
        self.assertNotIn("docker_cmd build", loader)
        installer = (R1 / "scripts/install_and_start.sh").read_text()
        self.assertLess(installer.index("load_images.sh"), installer.index("start.sh"))
        gates = (R1 / "scripts/run_target_gates.sh").read_text()
        self.assertNotIn("prepare_base_image.sh", gates)
        self.assertNotIn("build_image.sh", gates)
        root_start = (R1.parent / "start.sh").read_text()
        self.assertIn("start_one.sh", root_start)
        for scheme in ("one", "two"):
            scheme_start = (R1.parent / f"start_{scheme}.sh").read_text()
            scheme_benchmark = (R1.parent / f"benchmark_{scheme}.sh").read_text()
            self.assertIn(f"DSV4_SCHEME={scheme}", scheme_start)
            self.assertIn("install_and_start.sh", scheme_start)
            self.assertIn(f"DSV4_SCHEME={scheme}", scheme_benchmark)
            self.assertIn("run_benchmark.sh", scheme_benchmark)
        packager = (R1 / "scripts/package_offline_release.sh").read_text()
        self.assertIn('"$BASE_IMAGE" "$R1_IMAGE" "$SOURCE_TEST_IMAGE"', packager)
        self.assertIn('image save "${images[@]}" >"$image_archive"', packager)
        self.assertNotIn("image save --output", packager)
        self.assertIn("offline-images.env", packager)
        self.assertIn("OFFLINE_BUILD_MAX_JOBS=4", packager)
        self.assertIn("gzip -n -1", packager)
        self.assertIn("OFFLINE_COMPRESSION_JOBS", packager)
        self.assertIn("pigz -n -1", packager)
        self.assertIn("delivery_paths=(", packager)
        self.assertIn("vllm/entrypoints/anthropic/serving.py", packager)
        self.assertIn("tests", packager)
        self.assertIn('cp "$staging/$project_name/START-HERE.md"', packager)

    def test_earliest_release_is_safely_converged(self):
        convergence = (R1 / "scripts/stop_legacy_containers.sh").read_text()
        for identity in (
            "dsv4-target-only-f8ea5bb",
            "dsv4-dspark-f8ea5bb",
            "f8ea5bb163c161ef38b401d055cc5fd4a934091a",
            "com.deepseek.bundle",
            "com.deepseek.mode",
            "com.deepseek.vllm.commit",
        ):
            self.assertIn(identity, convergence)
        self.assertIn("container stop --time 120", convergence)
        self.assertNotIn("container rm", convergence)
        start = (R1 / "scripts/start.sh").read_text()
        self.assertLess(
            start.index("stop_legacy_containers.sh"), start.index("preflight.sh")
        )
        gates = (R1 / "scripts/run_target_gates.sh").read_text()
        self.assertLess(
            gates.index("stop_legacy_containers.sh"),
            gates.index("run_source_tests.sh"),
        )
        stop = (R1 / "scripts/stop.sh").read_text()
        self.assertIn('"$R1_DIR/scripts/stop_legacy_containers.sh"', stop)

    def test_earliest_release_convergence_behavior(self):
        names = ("dsv4-target-only-f8ea5bb", "dsv4-dspark-f8ea5bb")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "bin"
            state = root / "docker-state"
            fake_bin.mkdir()
            state.mkdir()
            fake_docker = fake_bin / "docker"
            fake_docker.write_text(
                textwrap.dedent(
                    r"""
                    #!/usr/bin/env python3
                    import os
                    import sys
                    from pathlib import Path

                    args = sys.argv[1:]
                    state = Path(os.environ["FAKE_DOCKER_STATE"])
                    modes = {
                        "dsv4-target-only-f8ea5bb": "target-only",
                        "dsv4-dspark-f8ea5bb": "dspark",
                    }
                    if args == ["version"]:
                        raise SystemExit(0)
                    if args[:2] == ["container", "inspect"]:
                        name = args[-1]
                        running = state / f"{name}.running"
                        if name not in modes or not running.exists():
                            raise SystemExit(1)
                        if "--format" not in args:
                            raise SystemExit(0)
                        template = args[args.index("--format") + 1]
                        wrong = os.environ.get("FAKE_WRONG_OWNER") == name
                        if "com.deepseek.bundle" in template:
                            print("not-this-release" if wrong else
                                  "deepseek-v4-flash-a100-offline")
                        elif "com.deepseek.mode" in template:
                            print(modes[name])
                        elif "com.deepseek.vllm.commit" in template:
                            print("f8ea5bb163c161ef38b401d055cc5fd4a934091a")
                        elif template == "{{.Id}}":
                            print(f"id-{name}")
                        elif template == "{{.Image}}":
                            print(f"sha256:image-{name}")
                        elif template == "{{.State.Running}}":
                            print(running.read_text().strip())
                        else:
                            raise SystemExit(2)
                        raise SystemExit(0)
                    if args[:2] == ["container", "stop"]:
                        name = args[-1]
                        (state / f"{name}.running").write_text("false\n")
                        with (state / "stop.log").open("a") as handle:
                            handle.write(f"{name}\n")
                        print(name)
                        raise SystemExit(0)
                    raise SystemExit(2)
                    """
                ).lstrip()
            )
            fake_docker.chmod(0o755)
            for name in names:
                (state / f"{name}.running").write_text("true\n")

            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
            env["FAKE_DOCKER_STATE"] = str(state)
            env["RUNTIME_BASE"] = str(root / "runtime")
            env.pop("DSV4_FORCE_SUDO_DOCKER", None)
            script = R1 / "scripts/stop_legacy_containers.sh"
            converged = subprocess.run(
                [str(script)], env=env, check=False, capture_output=True, text=True
            )
            self.assertEqual(converged.returncode, 0, converged.stderr)
            self.assertIn("LEGACY_CONVERGENCE=PASS", converged.stdout)
            self.assertIn("found=2", converged.stdout)
            self.assertIn("stopped=2", converged.stdout)
            self.assertEqual((state / "stop.log").read_text().splitlines(), list(names))
            for name in names:
                self.assertEqual((state / f"{name}.running").read_text(), "false\n")
            evidence = (
                root / "runtime/one/results/legacy-container-convergence.txt"
            ).read_text()
            self.assertIn("running_after=false", evidence)
            self.assertTrue(all(name in evidence for name in names))

            (state / f"{names[0]}.running").write_text("true\n")
            (state / "stop.log").unlink()
            env["FAKE_WRONG_OWNER"] = names[0]
            rejected = subprocess.run(
                [str(script)], env=env, check=False, capture_output=True, text=True
            )
            self.assertEqual(rejected.returncode, 1)
            self.assertIn("unrecognized owner", rejected.stderr)
            self.assertEqual((state / f"{names[0]}.running").read_text(), "true\n")
            self.assertFalse((state / "stop.log").exists())

    def test_runtime_continuity_finalizer_detects_engine_restart(self):
        process = {
            "status": "pass",
            "engine_core_processes": [
                {"pid": 42, "comm": "VLLM::EngineCor", "start_time_ticks": 100}
            ],
        }
        container = {
            "id": "container-id",
            "restart_count": 0,
            "started_at": "2026-08-20T00:00:00Z",
            "status": "running",
            "oom_killed": False,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, value in (
                ("summary.json", {"status": "pass"}),
                ("process-before.json", process),
                ("process-after.json", process),
                ("container-before.json", container),
                ("container-after.json", container),
            ):
                (root / name).write_text(json.dumps(value))
            command = [
                sys.executable,
                str(R1 / "tests/finalize_stability_evidence.py"),
                "--summary",
                str(root / "summary.json"),
                "--process-before",
                str(root / "process-before.json"),
                "--process-after",
                str(root / "process-after.json"),
                "--container-before",
                str(root / "container-before.json"),
                "--container-after",
                str(root / "container-after.json"),
                "--harness-exit",
                "0",
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(summary["engine_core_restart_count"], 0)
            self.assertEqual(summary["runtime_continuity_status"], "pass")

            restarted = {
                **process,
                "engine_core_processes": [
                    {
                        "pid": 43,
                        "comm": "VLLM::EngineCor",
                        "start_time_ticks": 200,
                    }
                ],
            }
            (root / "summary.json").write_text(json.dumps({"status": "pass"}))
            (root / "process-after.json").write_text(json.dumps(restarted))
            failed = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
            self.assertEqual(failed.returncode, 1)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(summary["engine_core_restart_count"], 1)
            self.assertEqual(summary["runtime_continuity_status"], "fail")

            (root / "process-after.json").write_text(json.dumps(process))
            (root / "summary.json").unlink()
            missing = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
            self.assertEqual(missing.returncode, 1)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(summary["status"], "fail")
            self.assertFalse(summary["harness_summary_present"])
            self.assertEqual(summary["failure_category"], "harness_summary_missing")

    def test_source_test_summary_rejects_mega_moe_skip(self):
        suites = {
            "deepseek_v4_parser": "deepseek-v4-parser.xml",
            "parser_engine": "parser-engine.xml",
            "deepseek_v4_tokenizer": "deepseek-v4-tokenizer.xml",
            "deepseek_v4_lifecycle": "deepseek-v4-lifecycle.xml",
            "deepseek_v4_mega_moe": "deepseek-v4-mega-moe.xml",
            "anthropic_conversion": "anthropic-conversion.xml",
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, filename in suites.items():
                (root / filename).write_text(
                    '<testsuite tests="1" failures="0" errors="0" skipped="0"/>'
                )
                (root / f"{name}.exit-code").write_text("0\n")
            command = [
                sys.executable,
                str(R1 / "tests/summarize_source_tests.py"),
                "--results-dir",
                str(root),
                "--output",
                str(root / "summary.json"),
            ]
            subprocess.run(command, check=True, capture_output=True, text=True)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(summary["status"], "pass")
            self.assertEqual(summary["test_executions"], 6)
            self.assertEqual(summary["unique_tests"], 5)

            (root / "deepseek-v4-mega-moe.xml").write_text(
                '<testsuite tests="1" failures="0" errors="0" skipped="1"/>'
            )
            failed = subprocess.run(
                command, check=False, capture_output=True, text=True
            )
            self.assertEqual(failed.returncode, 1)
            summary = json.loads((root / "summary.json").read_text())
            self.assertEqual(
                summary["suites"]["deepseek_v4_mega_moe"]["status"], "fail"
            )

    def test_tokenizer_runtime_compares_checkpoint_config(self):
        runtime_check = (R1 / "tests/tokenizer_runtime_check.py").read_text()
        self.assertIn("observed != 129_280", runtime_check)
        self.assertIn("observed != config_vocab_size", runtime_check)
        self.assertIn("observed != 129_283", runtime_check)
        preflight = (R1 / "scripts/preflight.sh").read_text()
        self.assertIn("--fixed-base-rollback", preflight)

    def test_performance_wrapper_is_resumable(self):
        script = (R1 / "scripts/run_performance.sh").read_text()
        self.assertIn("performance-$SCHEME_ID", script)
        self.assertIn('--max-concurrency "$BENCHMARK_MAX_CONCURRENCY"', script)
        self.assertIn("tee -a", script)
        self.assertNotIn("performance-$timestamp", script)

        benchmark = load_benchmark_module()
        requested_urls: list[str] = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            @staticmethod
            def read():
                return b"{}"

        def fake_urlopen(request, **_kwargs):
            requested_urls.append(request.full_url)
            return FakeResponse()

        original_urlopen = benchmark.urllib.request.urlopen
        benchmark.urllib.request.urlopen = fake_urlopen
        try:
            client = benchmark.JsonHttpClient(
                "http://127.0.0.1:8005/v1", "", 10
            )
            client.json("/tokenize", {})
            client.json("/v1/models")
            with client.request("/v1/chat/completions", {}, stream=True):
                pass
        finally:
            benchmark.urllib.request.urlopen = original_urlopen
        self.assertEqual(
            requested_urls,
            [
                "http://127.0.0.1:8005/tokenize",
                "http://127.0.0.1:8005/v1/models",
                "http://127.0.0.1:8005/v1/chat/completions",
            ],
        )

        captured: list[tuple[int, int, int, int]] = []

        class FakeClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def json(self, _path):
                return {"data": [{"id": "model"}]}

        def fake_run_cell(
            _client,
            _model,
            row,
            prompt_repetitions,
            calibrated_count,
            max_output_tokens,
            tolerance,
        ):
            captured.append(
                (
                    prompt_repetitions,
                    calibrated_count,
                    max_output_tokens,
                    tolerance,
                )
            )
            row["status"] = "complete"
            row["requests_success"] = 1
            row["requests_failed"] = 0
            return row

        with tempfile.TemporaryDirectory() as directory:
            benchmark.CONTEXTS = (10_000,)
            benchmark.CONCURRENCIES = (1,)
            benchmark.parse_args = lambda: benchmark.argparse.Namespace(
                base_url="http://127.0.0.1:8005/v1",
                api_key="",
                model="model",
                output=Path(directory) / "matrix.csv",
                repetitions=1,
                max_output_tokens=32,
                request_timeout=10,
                token_tolerance=32,
                plan_only=False,
                overwrite=False,
                rerun_failed=False,
            )
            benchmark.JsonHttpClient = FakeClient
            benchmark.calibrate_prompt = lambda *_args: (123, 10_000)
            benchmark.run_cell = fake_run_cell
            self.assertEqual(benchmark.main(), 0)
        self.assertEqual(captured, [(123, 10_000, 32, 32)])

    def test_overlay_manifests_match_current_files(self):
        lines = (R1 / "manifests/r1-python.sha256").read_text().splitlines()
        self.assertEqual(len(lines), 8)
        for line in lines:
            expected, relative = line.split("  ", 1)
            digest = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
            self.assertEqual(digest, expected, relative)

    def test_backport_scope_and_exclusions_are_locked(self):
        lock = json.loads((R1 / "manifests/source-lock.json").read_text())
        self.assertEqual(
            lock["base_commit"],
            "12810046c799cbe874967e19b1c0fa134ab7b209",
        )
        exclusions = " ".join(lock["explicit_exclusions"])
        for forbidden in ("#47629", "#50645", "4f2aae2", "speculative"):
            self.assertIn(forbidden, exclusions)

    def test_build_host_evidence_is_explicit_about_gpu_gap(self):
        evidence = json.loads(
            (R1 / "manifests/build-host-test-evidence.json").read_text()
        )
        self.assertEqual(evidence["unique_tests_passed"], 3885)
        self.assertEqual(evidence["cuda_mega_moe"]["passed"], 0)
        self.assertEqual(evidence["cuda_mega_moe"]["skipped"], 5)
        self.assertTrue(evidence["cuda_mega_moe"]["target_gate_required"])
        self.assertFalse(evidence["environment"]["target_gpu_claims_allowed"])

    def test_fixed_base_is_ancestor(self):
        base = "12810046c799cbe874967e19b1c0fa134ab7b209"
        if (ROOT / ".git").exists() and shutil.which("git"):
            available = subprocess.run(
                ["git", "cat-file", "-e", f"{base}^{{commit}}"],
                cwd=ROOT,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            if available.returncode != 0:
                self.skipTest("public source-only history omits the fixed base commit")
            subprocess.run(
                ["git", "merge-base", "--is-ancestor", base, "HEAD"],
                cwd=ROOT,
                check=True,
            )
            return

        # Target deliveries intentionally have no Git metadata or host Git.
        # Verify that developer-side packaging cannot succeed without the same
        # real ancestry check.
        packager = (R1 / "scripts/package_release.sh").read_text()
        self.assertIn(f"base={base}", packager)
        self.assertIn('merge-base --is-ancestor "$base" "$head"', packager)

    def test_client_context_and_wire_protocol(self):
        config = (R1 / "clients/codex/config.toml.example").read_text()
        self.assertIn('wire_api = "responses"', config)
        self.assertIn("model_context_window = 262144", config)
        claude = (R1 / "clients/claude/run_claude.sh").read_text()
        self.assertIn("ANTHROPIC_BASE_URL", claude)
        self.assertIn("ANTHROPIC_AUTH_TOKEN", claude)
        self.assertIn("ANTHROPIC_DEFAULT_MODEL", claude)
        self.assertIn("CLAUDE_CODE_ENABLE_GATEWAY_MODEL_DISCOVERY", claude)
        self.assertIn("CLAUDE_MODEL_ALIAS", claude)

    def test_executable_entrypoints(self):
        paths = list((R1 / "scripts").glob("*.sh"))
        paths += list((R1 / "clients").glob("*/*.sh"))
        paths += [
            ROOT / name
            for name in (
                "start_one.sh",
                "start_two.sh",
                "benchmark_one.sh",
                "benchmark_two.sh",
                "report_one.sh",
                "report_two.sh",
                "status_one.sh",
                "status_two.sh",
            )
        ]
        for path in paths:
            self.assertTrue(os.access(path, os.X_OK), f"not executable: {path}")

    def test_release_packager_embeds_exact_git_revision(self):
        packager = (R1 / "scripts/package_release.sh").read_text()
        self.assertIn("delivery-git-sha.txt", packager)
        self.assertIn("--add-virtual-file", packager)
        self.assertIn('git -C "$ROOT_DIR" diff --quiet', packager)
        self.assertIn("ls-files --others --exclude-standard", packager)
        self.assertIn("sha256sum", packager)


if __name__ == "__main__":
    unittest.main(verbosity=2)

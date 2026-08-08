from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import uuid

import r13_lazy_backend_adapter as lazy_adapter
import r13_local_batch_runner as batch
import r13_local_development_manifest as local_manifest
import r13_runner_core as core


MVP_DIR = Path(__file__).resolve().parent
PROJECT = MVP_DIR.parent
WORKSPACE = PROJECT.parent


def digest(label: str) -> str:
    return hashlib.sha256(label.encode("utf-8")).hexdigest()


def minimal_contract() -> lazy_adapter.StaticAdapterContract:
    return lazy_adapter.StaticAdapterContract(
        schema_version=lazy_adapter.SCHEMA_VERSION,
        status="TEST_NO_MODEL",
        protocol_sha256=digest("protocol"),
        update_recipe_sha256=digest("recipe"),
        source_assets_sha256=digest("source"),
        model_inventory_sha256=digest("inventory"),
        runtime_environment_reference_sha256=digest("runtime"),
        model_repository="repo",
        model_revision=digest("revision")[:40],
        ordered_stack_ids=core.STACK_IDS,
        source_identities=core.SOURCE_IDENTITIES,
        ordered_candidate_set=core.ORDERED_CANDIDATE_SET,
        source_stacks=(),
        model_execution_authorized=False,
        production_backend_bound=False,
        full_dependency_content_hash_bound=False,
    )


def target_panels() -> tuple[core.TargetPanelBinding, ...]:
    return tuple(
        core.TargetPanelBinding(
            arm=arm,
            target_panel_sha256=digest(f"panel|{arm}"),
            ordered_rows=tuple(
                core.TargetRowBinding(
                    canonical_instance_id=f"canonical-{index}",
                    row_id=f"{arm}-row-{index}",
                    panel_row_sha256=digest(f"{arm}|row|{index}"),
                )
                for index in range(7)
            ),
        )
        for arm in core.ARMS
    )


def fake_manifest(output_root: Path) -> dict[str, object]:
    output_rel = output_root.relative_to(WORKSPACE).as_posix()
    artifacts = {
        role: {"path": f"fake/{role}.json", "sha256": digest(role), "byte_length": 1}
        for role in local_manifest.ALL_ARTIFACT_ROLES
    }
    return {
        "run_id": str(uuid.uuid4()),
        "ordered_process_ids": list(local_manifest.ORDERED_PROCESS_IDS),
        "process_plan": {"counts": dict(local_manifest.EXPECTED_COUNTS)},
        "output": {"root": output_rel},
        "artifacts": artifacts,
    }


class FakeCanaryBackend:
    captured: "FakeCanaryBackend | None" = None

    def __init__(self, **kwargs: object) -> None:
        type(self).captured = self
        self.kwargs = kwargs
        self.permit = None
        self.receipt = None

    def initialize_local(self, permit: object) -> None:
        self.permit = permit

    def run_technical_canary(self, *, receipt_path: Path) -> dict[str, object]:
        self.receipt = receipt_path
        return {"status": "FAKE_NO_MODEL_CANARY"}


class FakeWorkerBackend:
    captured: "FakeWorkerBackend | None" = None

    def __init__(self, **kwargs: object) -> None:
        type(self).captured = self
        self.kwargs = kwargs
        self.calls: list[str] = []
        self._initial = digest("initial")

    @property
    def initial_parameter_hash(self) -> str:
        return self._initial

    @property
    def model_action_counts(self) -> dict[str, int]:
        return dict(batch.EXPECTED_WORKER_MODEL_ACTION_COUNTS)

    def initialize_local(self, permit: object) -> None:
        self.calls.append("initialize")

    def attest_existing_canary_receipt(self, *, receipt_path: Path) -> str:
        self.calls.append("attest")
        return digest("canary_receipt")

    def activate_scientific_manifest(self, **kwargs: object) -> None:
        self.calls.append("activate")


class BatchRunnerTests(unittest.TestCase):
    def test_import_under_i_b_has_zero_ml_imports(self) -> None:
        python = WORKSPACE / ".mvp-venv" / "Scripts" / "python.exe"
        code = (
            "import sys;"
            f"sys.path.insert(0,{str(MVP_DIR)!r});"
            "import r13_local_batch_runner;"
            "forbidden={'torch','transformers','peft','numpy'};"
            "assert not (forbidden & set(sys.modules));"
            "print('IMPORT_NO_MODEL_ACTION')"
        )
        completed = subprocess.run(
            [str(python), "-I", "-B", "-c", code],
            cwd=WORKSPACE,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "IMPORT_NO_MODEL_ACTION")

    def test_canary_contract_contains_no_r13_cells_or_target_provider(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT) as temporary:
            receipt = Path(temporary) / "canary.json"
            result = batch.run_technical_canary(
                project_root=PROJECT,
                model_dir=WORKSPACE / "models" / "SmolLM2-360M-Instruct-a10cc151",
                receipt_path=receipt,
                backend_factory=FakeCanaryBackend,
            )
        backend = FakeCanaryBackend.captured
        self.assertIsNotNone(backend)
        contract = backend.kwargs["static_contract"]  # type: ignore[union-attr]
        self.assertEqual(contract.ordered_stack_ids, ())
        self.assertEqual(contract.source_identities, ())
        self.assertEqual(contract.source_stacks, ())
        self.assertIsNone(backend.kwargs["target_payload_provider"])  # type: ignore[union-attr]
        self.assertEqual(result, {"status": "FAKE_NO_MODEL_CANARY"})

    def test_worker_builds_real_runner_contract_and_exclusive_path_shape(self) -> None:
        token = uuid.uuid4().hex
        output_root = PROJECT / "authorized_runs" / f"r13_local_dev_ab_test_{token}"
        output_root.mkdir(parents=True)
        try:
            manifest = fake_manifest(output_root)
            manifest_file = output_root.parent / f"manifest_{token}.json"
            manifest_file.write_bytes(b"{}\n")
            manifest_hash = batch.sha256_file(manifest_file)
            stub = output_root.parent / f"stub_{token}.json"
            stub.write_bytes(b"x")
            manifest["artifacts"]["canary_receipt"]["sha256"] = digest("canary_receipt")  # type: ignore[index]
            context = batch.WorkerStaticContext(
                static_contract=minimal_contract(),
                target_payload_provider=object(),
                target_panels_by_stack={stack: target_panels() for stack in core.STACK_IDS},
                bindings={key: digest(key) for key in core.BINDING_KEYS},
            )
            captured: dict[str, object] = {}

            def fake_run(contract: core.R13RunContract, backend: object, *, clock: object) -> dict[str, object]:
                captured["contract"] = contract
                captured["backend"] = backend
                return {"fake": "process"}

            def fake_write(path: Path, value: object) -> None:
                captured["path"] = path
                captured["value"] = value

            with mock.patch.object(batch, "_artifact_path", return_value=stub), mock.patch.object(
                batch.runner, "run_stack_process", side_effect=fake_run
            ):
                result = batch.run_worker(
                    project_root=PROJECT,
                    manifest_path=manifest_file,
                    expected_manifest_sha256=manifest_hash,
                    process_id=local_manifest.ORDERED_PROCESS_IDS[0],
                    model_dir=WORKSPACE / "models" / "SmolLM2-360M-Instruct-a10cc151",
                    backend_factory=FakeWorkerBackend,
                    context_builder=lambda **_: context,
                    manifest_reader=lambda _: manifest,  # type: ignore[arg-type]
                    manifest_verifier=lambda *args, **kwargs: None,
                    result_writer=fake_write,
                )
            contract = captured["contract"]
            self.assertIsInstance(contract, core.R13RunContract)
            self.assertEqual(contract.bundle_run_id, manifest["run_id"])
            self.assertEqual(contract.initial_parameter_hash, digest("initial"))
            self.assertEqual(contract.target_panels, target_panels())
            self.assertEqual(
                captured["path"].name,
                batch.worker_result_filename(1, core.STACK_IDS[0], "A"),
            )
            self.assertEqual(result, {"fake": "process"})
            self.assertEqual(FakeWorkerBackend.captured.calls[:3], ["initialize", "attest", "activate"])
        finally:
            if output_root.exists():
                shutil.rmtree(output_root)
            for candidate in output_root.parent.glob(f"*_{token}.*"):
                candidate.unlink()

    def test_coordinate_is_serial_reads_only_after_eight_and_emits_strict_bundle(self) -> None:
        token = uuid.uuid4().hex
        output_root = PROJECT / "authorized_runs" / f"r13_local_dev_ab_test_{token}"
        manifest = fake_manifest(output_root)
        manifest_file = output_root.parent / f"manifest_{token}.json"
        manifest_file.parent.mkdir(parents=True, exist_ok=True)
        manifest_file.write_bytes(b"{}\n")
        manifest_hash = batch.sha256_file(manifest_file)
        launches: list[batch.WorkerLaunch] = []
        subprocess_count = 0
        read_count = 0
        progress: list[str] = []
        written: dict[str, object] = {}

        def command_builder(launch: batch.WorkerLaunch) -> list[str]:
            launches.append(launch)
            return ["fake-worker", str(launch.process_index)]

        class Completed:
            returncode = 0

        def fake_subprocess(command: list[str], **kwargs: object) -> Completed:
            nonlocal subprocess_count
            index = int(command[1]) - 1
            launches[index].result_path.write_bytes(b"{}\n")
            subprocess_count += 1
            return Completed()

        def fake_read(path: Path) -> dict[str, object]:
            nonlocal read_count
            self.assertEqual(subprocess_count, 8)
            read_count += 1
            launch = next(item for item in launches if item.result_path == path)
            executions = [
                {
                    "execution_event_id": f"run-{launch.process_index}|event|{6 + 4 * i:04d}",
                    "execution_key": f"{launch.replicate_id}|{launch.stack_id}|Z7_PLUS{i + 1}",
                    "source_rule_identity": f"Z7_PLUS{i + 1}",
                    "update_parameter_hash": digest(f"update|{launch.stack_id}|{i}"),
                }
                for i in range(5)
            ]
            return {
                "bundle_run_id": manifest["run_id"],
                "bindings": {key: digest(key) for key in core.BINDING_KEYS},
                "source_update_executions": executions,
            }

        def fake_bundle_write(path: Path, value: object) -> None:
            written["path"] = path
            written["value"] = value

        try:
            with mock.patch.object(batch, "_read_canonical_process", side_effect=fake_read), mock.patch.object(
                batch.runner,
                "validate_process_collection",
                return_value={"bundle_run_id": manifest["run_id"], "counts": {"os_processes": 8}},
            ):
                bundle = batch.coordinate(
                    project_root=PROJECT,
                    manifest_path=manifest_file,
                    expected_manifest_sha256=manifest_hash,
                    command_builder=command_builder,
                    subprocess_runner=fake_subprocess,
                    progress_writer=progress.append,
                    clock=lambda: datetime(2026, 8, 6, tzinfo=timezone.utc),
                    manifest_reader=lambda _: manifest,  # type: ignore[arg-type]
                    manifest_verifier=lambda *args, **kwargs: None,
                    bundle_writer=fake_bundle_write,
                )
            self.assertEqual(subprocess_count, 8)
            self.assertEqual(read_count, 8)
            self.assertEqual([item.process_id for item in launches], list(local_manifest.ORDERED_PROCESS_IDS))
            self.assertEqual(set(bundle), set(batch.BUNDLE_KEYS))
            self.assertEqual(bundle["model_action_counts"], batch.EXPECTED_BATCH_MODEL_ACTION_COUNTS)
            self.assertEqual(len(bundle["events"]), 40)
            self.assertNotIn("classification", str(bundle).lower())
            self.assertEqual(written["path"], output_root / batch.BUNDLE_FILENAME)
            self.assertEqual(len(progress), 16)
            self.assertTrue(all("score" not in line.lower() for line in progress))
        finally:
            if output_root.exists():
                shutil.rmtree(output_root)
            if manifest_file.exists():
                manifest_file.unlink()

    def test_default_worker_argv_is_exact_isolated_and_hash_bound(self) -> None:
        manifest_file = PROJECT / "formal_g1_development_r1" / "R13_CURRENT_STATE_R2.json"
        launch = batch.WorkerLaunch(
            process_index=1,
            process_id=local_manifest.ORDERED_PROCESS_IDS[0],
            stack_id=core.STACK_IDS[0],
            replicate_id="A",
            project_root=PROJECT,
            manifest_path=manifest_file,
            manifest_sha256=digest("manifest"),
            output_root=PROJECT / "authorized_runs" / "r13_local_dev_ab_unused",
            result_path=PROJECT / "unused.json",
        )
        expected_python = (WORKSPACE / ".mvp-venv" / "Scripts" / "python.exe").resolve()
        with mock.patch.object(batch.sys, "executable", str(expected_python)):
            command = list(batch._default_worker_command_builder(launch))
        self.assertEqual(Path(command[0]).resolve(), expected_python)
        self.assertEqual(command[1:3], ["-I", "-B"])
        self.assertEqual(command[3], Path(batch.__file__).resolve().relative_to(WORKSPACE).as_posix())
        self.assertEqual(command[4], "worker")
        self.assertEqual(command[command.index("--manifest-sha256") + 1], digest("manifest"))
        self.assertEqual(command[command.index("--process-id") + 1], launch.process_id)


if __name__ == "__main__":
    unittest.main()

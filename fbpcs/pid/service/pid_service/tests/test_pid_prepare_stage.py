#!/usr/bin/env python3
# Copyright (c) Meta Platforms, Inc. and affiliates.
#
# This source code is licensed under the MIT license found in the
# LICENSE file in the root directory of this source tree.

# pyre-strict
import unittest
from typing import Tuple, Dict, Union
from unittest.mock import MagicMock, patch

from fbpcp.entity.container_instance import ContainerInstance, ContainerInstanceStatus
from fbpcs.data_processing.pid_preparer.union_pid_preparer_cpp import (
    CppUnionPIDDataPreparerService,
)
from fbpcs.pcf.tests.async_utils import to_sync
from fbpcs.pid.entity.pid_instance import PIDStageStatus
from fbpcs.pid.entity.pid_stages import UnionPIDStage
from fbpcs.pid.service.pid_service.pid_prepare_stage import PIDPrepareStage
from fbpcs.pid.service.pid_service.pid_stage import PIDStage
from fbpcs.pid.service.pid_service.pid_stage_input import PIDStageInput
from libfb.py.asyncio.mock import AsyncMock
from libfb.py.testutil import data_provider


def data_test_prepare() -> Tuple[
    Dict[str, Union[ContainerInstanceStatus, bool]],
    Dict[str, Union[ContainerInstanceStatus, bool]],
    Dict[str, Union[ContainerInstanceStatus, bool]],
]:
    return (
        {
            "wait_for_containers": True,
            "expected_container_status": ContainerInstanceStatus.COMPLETED,
        },
        {
            "wait_for_containers": True,
            "expected_container_status": ContainerInstanceStatus.FAILED,
        },
        {
            "wait_for_containers": False,
            "expected_container_status": ContainerInstanceStatus.STARTED,
        },
    )


def data_test_run() -> Tuple[
    Dict[str, bool],
    Dict[str, bool],
]:
    return ({"wait_for_containers": True}, {"wait_for_containers": False})


class TestPIDPrepareStage(unittest.TestCase):
    @data_provider(data_test_prepare)
    @patch("fbpcs.pid.repository.pid_instance.PIDInstanceRepository")
    @to_sync
    async def test_prepare(
        self,
        mock_instance_repo: unittest.mock.MagicMock,
        wait_for_containers: bool,
        expected_container_status: ContainerInstanceStatus,
    ) -> None:
        with patch.object(
            CppUnionPIDDataPreparerService, "prepare_on_container_async"
        ) as mock_prepare_on_container_async, patch.object(
            PIDStage, "update_instance_containers"
        ):
            container = ContainerInstance(
                instance_id="123",
                ip_address="192.0.2.0",
                status=expected_container_status,
            )
            mock_prepare_on_container_async.return_value = container
            stage = PIDPrepareStage(
                stage=UnionPIDStage.PUBLISHER_PREPARE,
                instance_repository=mock_instance_repo,
                storage_svc="STORAGE",  # pyre-ignore
                onedocker_svc="ONEDOCKER",  # pyre-ignore
                onedocker_binary_config=MagicMock(
                    task_definition="offline-task:1#container",
                    tmp_directory="/tmp/",
                    binary_version="latest",
                ),
            )

            res = await stage.prepare(
                instance_id="123",
                input_path="in",
                output_path="out",
                num_shards=1,
                fail_fast=False,
                wait_for_containers=wait_for_containers,
            )
            self.assertEqual(
                PIDStage.get_stage_status_from_containers([container]),
                res,
            )

    @data_provider(data_test_run)
    @to_sync
    @patch(
        "fbpcs.private_computation.service.run_binary_base_service.RunBinaryBaseService.wait_for_containers_async"
    )
    @patch("fbpcp.service.storage.StorageService")
    @patch("fbpcs.pid.repository.pid_instance.PIDInstanceRepository")
    @patch("fbpcp.service.onedocker.OneDockerService")
    async def test_run(
        self,
        mock_onedocker_svc: unittest.mock.MagicMock,
        mock_instance_repo: unittest.mock.MagicMock,
        mock_storage_svc: unittest.mock.MagicMock,
        mock_wait_for_containers_async: unittest.mock.MagicMock,
        wait_for_containers: bool,
    ) -> None:
        ip = "192.0.2.0"
        container = ContainerInstance(
            instance_id="123", ip_address=ip, status=ContainerInstanceStatus.STARTED
        )

        mock_onedocker_svc.start_containers = MagicMock(return_value=[container])
        mock_onedocker_svc.wait_for_pending_containers = AsyncMock(
            return_value=[container]
        )

        container.status = (
            ContainerInstanceStatus.COMPLETED
            if wait_for_containers
            else ContainerInstanceStatus.STARTED
        )
        mock_wait_for_containers_async.return_value = [container]

        stage = PIDPrepareStage(
            stage=UnionPIDStage.PUBLISHER_PREPARE,
            instance_repository=mock_instance_repo,
            storage_svc=mock_storage_svc,
            onedocker_svc=mock_onedocker_svc,
            onedocker_binary_config=MagicMock(
                task_definition="offline-task:1#container",
                tmp_directory="/tmp/",
                binary_version="latest",
            ),
        )
        instance_id = "444"
        stage_input = PIDStageInput(
            input_paths=["in"],
            output_paths=["out"],
            num_shards=2,
            instance_id=instance_id,
        )

        # Basic test: All good
        with patch.object(PIDPrepareStage, "files_exist") as mock_fe:
            mock_fe.return_value = True
            stage = PIDPrepareStage(
                stage=UnionPIDStage.PUBLISHER_PREPARE,
                instance_repository=mock_instance_repo,
                storage_svc=mock_storage_svc,
                onedocker_svc=mock_onedocker_svc,
                onedocker_binary_config=MagicMock(
                    task_definition="offline-task:1#container",
                    tmp_directory="/tmp/",
                    binary_version="latest",
                ),
            )
            status = await stage.run(
                stage_input, wait_for_containers=wait_for_containers
            )
            self.assertEqual(
                PIDStageStatus.COMPLETED
                if wait_for_containers
                else PIDStageStatus.STARTED,
                status,
            )

            self.assertEqual(mock_onedocker_svc.start_containers.call_count, 2)
            if wait_for_containers:
                self.assertEqual(mock_wait_for_containers_async.call_count, 2)
            else:
                mock_wait_for_containers_async.assert_not_called()
            mock_instance_repo.read.assert_called_with(instance_id)
            self.assertEqual(mock_instance_repo.read.call_count, 4)
            self.assertEqual(mock_instance_repo.update.call_count, 4)

        fail_fast = True
        with patch.object(PIDPrepareStage, "files_exist") as mock_fe, patch.object(
            PIDPrepareStage, "prepare"
        ) as mock_prepare:
            mock_fe.return_value = True
            stage_input.fail_fast = fail_fast
            status = await stage.run(
                stage_input, wait_for_containers=wait_for_containers
            )
            mock_prepare.assert_called_with(
                instance_id, "in", "out", 2, fail_fast, wait_for_containers, None
            )

        # Input not ready
        with patch.object(PIDPrepareStage, "files_exist") as mock_fe:
            mock_fe.return_value = False
            status = await stage.run(
                stage_input, wait_for_containers=wait_for_containers
            )
            self.assertEqual(PIDStageStatus.FAILED, status)

        # Multiple input paths (invariant exception)
        with patch.object(PIDPrepareStage, "files_exist") as mock_fe:
            with self.assertRaises(ValueError):
                mock_fe.return_value = True
                stage_input.input_paths = ["in1", "in2"]
                stage = PIDPrepareStage(
                    stage=UnionPIDStage.PUBLISHER_PREPARE,
                    instance_repository=mock_instance_repo,
                    storage_svc=mock_storage_svc,
                    onedocker_svc=mock_onedocker_svc,
                    onedocker_binary_config=MagicMock(
                        task_definition="offline-task:1#container",
                        tmp_directory="/tmp/",
                        binary_version="latest",
                    ),
                )
                status = await stage.run(
                    stage_input, wait_for_containers=wait_for_containers
                )

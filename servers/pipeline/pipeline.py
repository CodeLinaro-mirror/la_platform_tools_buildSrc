# Copyright 2026 - The Android Open Source Project
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import time

logger = logging.getLogger(__name__)

try:
    from pipeline.context import BuildContext
    from pipeline.stage import Stage
except ImportError:
    from context import BuildContext
    from stage import Stage


class Pipeline:
    """Orchestrates sequential execution of build & test stages with guaranteed logging and telemetry."""

    def __init__(self, ctx: BuildContext, stages: list[Stage]):
        self.ctx = ctx
        self.stages = stages

    def run(self) -> None:
        """Executes the pipeline stages sequentially."""
        pipeline_start = time.monotonic()
        stage_num = 0
        for stage in self.stages:
            if not stage.should_run(self.ctx):
                logger.info("Skipping stage: %s", stage.name)
                continue

            stage_num += 1
            logger.info("=" * 80)
            logger.info("STARTING STAGE %d: %s", stage_num, stage.name)
            logger.info("=" * 80)

            stage_start = time.monotonic()
            try:
                stage.pre_execute(self.ctx)
                stage.execute(self.ctx)
            except Exception as exc:
                elapsed = time.monotonic() - stage_start
                logger.error(
                    "Stage %d ('%s') failed after %0.1fs: %s",
                    stage_num,
                    stage.name,
                    elapsed,
                    exc,
                )
                exc._logged_by_pipeline = True
                try:
                    stage.on_failure(self.ctx, exc)
                except Exception as inner_exc:  # noqa: BLE001  # Guard against on_failure hooks crashing
                    logger.error("stage.on_failure failed: %s", inner_exc)
                finally:
                    self.ctx.copy_bazel_logs()
                raise

            elapsed = time.monotonic() - stage_start
            logger.info(
                "Stage %d ('%s') completed successfully in %0.1fs",
                stage_num,
                stage.name,
                elapsed,
            )

        # Ensure logs are collected on normal pipeline completion
        self.ctx.copy_bazel_logs()
        total_time = time.monotonic() - pipeline_start
        logger.info("All pipeline stages completed successfully in %0.1fs", total_time)

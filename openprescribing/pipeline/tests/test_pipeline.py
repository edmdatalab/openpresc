import json
import os

import mock
from django.conf import settings
from django.test import TestCase, override_settings
from pipeline.models import TaskLog
from pipeline.runner import in_progress, load_tasks, run_task


class PipelineTests(TestCase):
    def setUp(self):
        # Load tasks
        self.tasks = load_tasks()

        # Set up dummy files on filesystem
        for source_id, year_and_month, filename in [
            ["source_a", "2017_01", "source_a.csv"],
            ["source_a", "2017_02", "source_a.csv"],
            ["source_a", "2017_03", "source_a.csv"],
            ["source_b", "2017_01", "source_b_1701.csv"],
            ["source_b", "2017_02", "source_b_1702.csv"],
            ["source_b", "2017_03", "source_b_1703.csv"],
            ["source_c", "2017_01", "source_c1.csv"],
            ["source_c", "2017_01", "source_c2.csv"],
            ["source_c", "2017_02", "source_c1.csv"],
            ["source_c", "2017_02", "source_c2.csv"],
        ]:
            path = build_path(source_id, year_and_month, filename)
            dir_path = os.path.dirname(path)
            try:
                os.makedirs(dir_path)
            except OSError as e:
                import errno

                if e.errno != errno.EEXIST or not os.path.isdir(dir_path):
                    raise
            with open(path, "w") as f:
                f.write("1,2,3\n")

        # Set up dummy log data
        log_data = {
            "source_a": [
                {
                    "imported_file": build_path("source_a", "2017_01", "source_a.csv"),
                    "imported_at": "2017-01-01T12:00:00",
                },
                {
                    "imported_file": build_path("source_a", "2017_02", "source_a.csv"),
                    "imported_at": "2017-02-01T12:00:00",
                },
            ],
            "source_b": [
                {
                    "imported_file": build_path(
                        "source_b", "2017_01", "source_b_1701.csv"
                    ),
                    "imported_at": "2017-01-01T12:00:00",
                },
                {
                    "imported_file": build_path(
                        "source_b", "2017_02", "source_b_1702.csv"
                    ),
                    "imported_at": "2017-02-01T12:00:00",
                },
            ],
            "source_c": [
                {
                    "imported_file": build_path("source_c", "2017_01", "source_c2.csv"),
                    "imported_at": "2017-01-01T12:00:00",
                },
                {
                    "imported_file": build_path("source_c", "2017_02", "source_c2.csv"),
                    "imported_at": "2017-02-01T12:00:00",
                },
            ],
        }

        with open(settings.PIPELINE_IMPORT_LOG_PATH, "w") as f:
            json.dump(log_data, f)

    def test_task_initialisation(self):
        task = self.tasks["fetch_source_a"]
        self.assertEqual(task.name, "fetch_source_a")
        self.assertEqual(task.task_type, "manual_fetch")
        self.assertEqual(task.source_id, "source_a")
        self.assertEqual(task.dependencies, [])

        task = self.tasks["convert_source_a"]
        self.assertEqual(task.dependency_names, ["fetch_source_a"])

    def test_load_real_tasks(self):
        # We're just checking that no exceptions get raised here
        path = os.path.join(settings.APPS_ROOT, "pipeline", "metadata")
        with override_settings(PIPELINE_METADATA_DIR=path):
            load_tasks()

    def test_run_real_tasks(self):
        # We're not actually going to run the management commands, but we're
        # going to check that the management commands exist and can be run with
        # the given input
        path = os.path.join(settings.APPS_ROOT, "pipeline", "metadata")
        with override_settings(PIPELINE_METADATA_DIR=path):
            tasks = load_tasks()

        with mock.patch("django.core.management.base.BaseCommand.execute"):
            for task in tasks.by_type("auto_fetch"):
                task.run(2017, 7)

            with mock.patch(
                "pipeline.runner.Task.unimported_paths", return_value=["/some/path"]
            ):
                for task in tasks.by_type("convert"):
                    task.run(2017, 7)

            with mock.patch(
                "pipeline.runner.Task.unimported_paths", return_value=["/some/path"]
            ):
                for task in tasks.by_type("import"):
                    task.run(2017, 7)

            for task in tasks.by_type("post_process"):
                task.run(2017, 7, last_imported="2017_01")

    def test_tasks_by_type(self):
        tasks = self.tasks.by_type("manual_fetch")
        self.assertIn("fetch_source_a", [task.name for task in tasks])

        tasks = self.tasks.by_type("auto_fetch")
        self.assertIn("fetch_source_b", [task.name for task in tasks])

    def test_tasks_ordered(self):
        task_names = [task.name for task in self.tasks.ordered()]
        for name1, name2 in [
            ["fetch_source_a", "convert_source_a"],
            ["convert_source_a", "import_source_a"],
            ["fetch_source_b", "import_source_b"],
            ["import_source_a", "import_source_b"],
            ["fetch_source_c", "import_source_c1"],
            ["import_source_a", "import_source_c1"],
            ["import_source_b", "import_source_c1"],
            ["fetch_source_c", "import_source_c2"],
            ["import_source_c1", "import_source_c2"],
            ["import_source_a", "post_process"],
            ["import_source_b", "post_process"],
            ["import_source_c1", "post_process"],
        ]:
            self.assertTrue(task_names.index(name1) < task_names.index(name2))

    def test_tasks_by_type_ordered(self):
        tasks = self.tasks.by_type("import").ordered()
        task_names = [task.name for task in tasks]
        expected_output = [
            "import_source_a",
            "import_source_b",
            "import_source_c1",
            "import_source_c2",
        ]
        self.assertEqual(task_names, expected_output)

    def test_tasks_ordered_by_type(self):
        tasks = self.tasks.ordered().by_type("import")
        task_names = [task.name for task in tasks]
        expected_output = [
            "import_source_a",
            "import_source_b",
            "import_source_c1",
            "import_source_c2",
        ]
        self.assertEqual(task_names, expected_output)

    def test_source_initialisation(self):
        source = self.tasks["import_source_a"].source
        self.assertEqual(source.name, "source_a")
        self.assertEqual(source.title, "Source A")

    def test_tasks_that_use_raw_source_data(self):
        source_a = self.tasks["fetch_source_a"].source
        self.assertEqual(
            [task.name for task in source_a.tasks_that_use_raw_source_data()],
            ["convert_source_a"],
        )

        source_c = self.tasks["fetch_source_c"].source
        self.assertEqual(
            [task.name for task in source_c.tasks_that_use_raw_source_data()],
            ["import_source_c1", "import_source_c2"],
        )

    def test_filename_pattern(self):
        task = self.tasks["convert_source_a"]
        self.assertEqual(task.filename_pattern(), "source_a.csv")

    def test_imported_paths(self):
        task = self.tasks["convert_source_a"]
        expected_output = [
            build_path("source_a", "2017_01", "source_a.csv"),
            build_path("source_a", "2017_02", "source_a.csv"),
        ]
        self.assertEqual(task.imported_paths(), expected_output)

        task = self.tasks["import_source_b"]
        expected_output = [
            build_path("source_b", "2017_01", "source_b_1701.csv"),
            build_path("source_b", "2017_02", "source_b_1702.csv"),
        ]
        self.assertEqual(task.imported_paths(), expected_output)

        task = self.tasks["import_source_c1"]
        self.assertEqual(task.imported_paths(), [])

    def test_set_last_imported_path(self):
        task = self.tasks["import_source_b"]
        path = build_path("source_b", "2017_03", "source_b_1703.csv")
        task.set_last_imported_path(path)
        expected_output = [
            build_path("source_b", "2017_01", "source_b_1701.csv"),
            build_path("source_b", "2017_02", "source_b_1702.csv"),
            build_path("source_b", "2017_03", "source_b_1703.csv"),
        ]
        self.assertEqual(task.imported_paths(), expected_output)

        # According to the log data in setUp(), no data has been imported for
        # source_c yet
        task1 = self.tasks["import_source_c1"]
        path = build_path("source_c", "2017_03", "source_c1.csv")
        task1.set_last_imported_path(path)
        expected_output = [build_path("source_c", "2017_03", "source_c1.csv")]
        self.assertEqual(task1.imported_paths(), expected_output)
        expected_output = [
            build_path("source_b", "2017_01", "source_b_1701.csv"),
            build_path("source_b", "2017_02", "source_b_1702.csv"),
            build_path("source_b", "2017_03", "source_b_1703.csv"),
        ]
        self.assertEqual(task.imported_paths(), expected_output)

    def test_input_paths(self):
        task = self.tasks["import_source_b"]
        expected_output = [
            build_path(
                "source_b", "2017_{}".format(month), "source_b_17{}.csv".format(month)
            )
            for month in ["01", "02", "03"]
        ]
        self.assertEqual(task.input_paths(), expected_output)

    def test_unimported_paths(self):
        task = self.tasks["import_source_b"]
        expected_output = [build_path("source_b", "2017_03", "source_b_1703.csv")]
        self.assertEqual(task.unimported_paths(), expected_output)

    def test_manual_fetch_instructions(self):
        task = self.tasks["fetch_source_a"]
        expected_output = """
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
You should now locate the latest data for source_a, if available
You should save it at:
    {data_basedir}/source_a/YYYY_MM
The last imported data can be found at:
    {data_basedir}/source_a/2017_02/source_a.csv
""".strip().format(data_basedir=settings.PIPELINE_DATA_BASEDIR)
        output = task.manual_fetch_instructions()
        self.assertEqual(output, expected_output)

        task = self.tasks["fetch_source_c"]
        expected_output = """
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
You should now locate the latest data for source_c, if available
You should save it at:
    {data_basedir}/source_c/YYYY_MM
The last imported data can be found at:
    <never imported>
    {data_basedir}/source_c/2017_02/source_c2.csv
""".strip().format(data_basedir=settings.PIPELINE_DATA_BASEDIR)
        output = task.manual_fetch_instructions()
        self.assertEqual(output, expected_output)

    def test_manual_fetch_instructions_with_real_data(self):
        path = os.path.join(settings.APPS_ROOT, "pipeline", "metadata")
        with override_settings(PIPELINE_METADATA_DIR=path):
            tasks = load_tasks()

        # We're just checking that no exceptions get raised here
        for task in tasks.by_type("manual_fetch"):
            task.manual_fetch_instructions()

    def test_run_auto_fetch(self):
        task = self.tasks["fetch_source_b"]
        with mock.patch("pipeline.runner.call_command") as cc:
            task.run(2017, 7)
            cc.assert_called_with("fetch_source_b", "2017", "7", "--yes")

    def test_run_convert(self):
        task = self.tasks["convert_source_a"]
        path = build_path("source_a", "2017_03", "source_a.csv")
        with mock.patch("pipeline.runner.call_command") as cc:
            task.run(2017, 7)
            cc.assert_called_with("convert_source_a", "--filename", path)

    def test_run_import(self):
        task = self.tasks["import_source_c1"]
        expected_calls = []
        for year_and_month in ["2017_01", "2017_02"]:
            path = build_path("source_c", year_and_month, "source_c1.csv")
            call = mock.call("import_source_c", "--filename", path)
            expected_calls.append(call)

        with mock.patch("pipeline.runner.call_command") as cc:
            task.run(2017, 7)
            cc.assert_has_calls(expected_calls)

    def test_run_post_process(self):
        task = self.tasks["post_process"]
        with mock.patch("pipeline.runner.call_command") as cc:
            task.run(2017, 7, "2017_01")
            cc.assert_called_with("post_process", "2017_01")

    def test_run_task(self):
        task = self.tasks["fetch_source_b"]
        with mock.patch("pipeline.runner.call_command"):
            run_task(task, 2017, 7)

        log = TaskLog.objects.get(year=2017, month=7, task_name="fetch_source_b")
        self.assertEqual(log.status, "successful")
        self.assertIsNotNone(log.ended_at)

    def test_run_task_that_fails(self):
        task = self.tasks["fetch_source_b"]
        with self.assertRaises(KeyboardInterrupt):
            with mock.patch("pipeline.runner.call_command") as cc:
                cc.side_effect = KeyboardInterrupt
                run_task(task, 2017, 7)

        log = TaskLog.objects.get(year=2017, month=7, task_name="fetch_source_b")
        self.assertEqual(log.status, "failed")
        self.assertIsNotNone(log.ended_at)
        self.assertIn("KeyboardInterrupt", log.formatted_tb)

    def test_run_task_after_success(self):
        task = self.tasks["fetch_source_b"]
        with mock.patch("pipeline.runner.call_command") as cc:
            run_task(task, 2017, 7)

        with mock.patch("pipeline.runner.call_command") as cc:
            run_task(task, 2017, 7)
            cc.assert_not_called()

        logs = TaskLog.objects.filter(year=2017, month=7, task_name="fetch_source_b")
        self.assertEqual(1, logs.count())

    def test_run_task_after_failure(self):
        task = self.tasks["fetch_source_b"]
        with self.assertRaises(KeyboardInterrupt):
            with mock.patch("pipeline.runner.call_command") as cc:
                cc.side_effect = KeyboardInterrupt
                run_task(task, 2017, 7)

        with mock.patch("pipeline.runner.call_command") as cc:
            run_task(task, 2017, 7)

        logs = TaskLog.objects.filter(year=2017, month=7, task_name="fetch_source_b")
        self.assertEqual(2, logs.count())

    def test_in_progress_when_not_in_progress(self):
        TaskLog.objects.create(year=2017, month=7, task_name="task1")
        TaskLog.objects.create(year=2017, month=7, task_name="task2")
        TaskLog.objects.create(year=2017, month=7, task_name="fetch_and_import")
        TaskLog.objects.create(year=2017, month=8, task_name="task1")
        TaskLog.objects.create(year=2017, month=8, task_name="task2")
        TaskLog.objects.create(year=2017, month=8, task_name="fetch_and_import")
        self.assertFalse(in_progress())

    def test_in_progress_when_in_progress(self):
        TaskLog.objects.create(year=2017, month=7, task_name="task1")
        TaskLog.objects.create(year=2017, month=7, task_name="task2")
        TaskLog.objects.create(year=2017, month=7, task_name="fetch_and_import")
        TaskLog.objects.create(year=2017, month=8, task_name="task1")
        TaskLog.objects.create(year=2017, month=8, task_name="task2")
        TaskLog.objects.create(year=2017, month=8, task_name="fetch_and_import")
        TaskLog.objects.create(year=2017, month=9, task_name="task1")
        self.assertTrue(in_progress())


def build_path(source_id, year_and_month, filename):
    return os.path.join(
        settings.PIPELINE_DATA_BASEDIR, source_id, year_and_month, filename
    )

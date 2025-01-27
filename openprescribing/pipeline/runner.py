import datetime
import fnmatch
import glob
import json
import os
import re
import shlex
import textwrap
from collections import defaultdict

import networkx as nx
from django.conf import settings
from django.core.management import call_command as django_call_command
from gcutils.storage import Client as StorageClient

from openprescribing.slack import notify_slack
from openprescribing.utils import find_files

from .models import TaskLog


class Source(object):
    def __init__(self, name, attrs):
        self.name = name
        self.title = attrs["title"]
        self.data_dir = os.path.join(
            settings.PIPELINE_DATA_BASEDIR, attrs.get("data_dir", name)
        )

        self.publisher = attrs.get("publisher")
        self.publication_schedule = attrs.get("publication_schedule")
        self.publication_lag = attrs.get("publication_lag")
        self.notes = attrs.get("notes")
        self.index_url = attrs.get("index_url")
        self.urls = attrs.get("urls")

        self.tasks = TaskCollection()

    def add_task(self, task):
        self.tasks.add(task)

    def tasks_that_use_raw_source_data(self):
        tasks = self.tasks.by_type("convert")
        if not tasks:
            tasks = self.tasks.by_type("import")
        return tasks


class SourceCollection(object):
    def __init__(self, source_data):
        self._sources = {
            name: Source(name, attrs) for name, attrs in source_data.items()
        }

    def __getitem__(self, name):
        return self._sources[name]


class Task(object):
    def __init__(self, name, attrs):
        self.name = name
        self.task_type = attrs["type"]
        if self.task_type == "post_process":
            self.source_id = None
        else:
            self.source_id = attrs["source_id"]
        if self.task_type not in ["manual_fetch", "manual_fetch_skip_for_now"]:
            self.command = attrs["command"]
        if self.task_type not in [
            "manual_fetch",
            "manual_fetch_skip_for_now",
            "auto_fetch",
        ]:
            self.dependency_names = attrs["dependencies"]
        else:
            self.dependency_names = []

    def set_source(self, source):
        self.source = source

    def resolve_dependencies(self, task_collection):
        self.dependencies = [task_collection[name] for name in self.dependency_names]

    def filename_pattern(self):
        """Return pattern that matches the part of the task's command that
        should be substituted for the task's input filename."""
        filename_flags = [
            "filename",
            "ccg",
            "epraccur",
            "chem_file",
            "hscic_address",
            "month_from_prescribing_filename",
            "zip_path",
        ]

        cmd_parts = shlex.split(self.command)
        filename_idx = None
        for flag in filename_flags:
            try:
                filename_idx = cmd_parts.index("--%s" % flag) + 1
            except ValueError:
                pass
        assert filename_idx is not None
        return cmd_parts[filename_idx]

    def imported_paths(self):
        """Return a list of import records for all imported data for this
        task."""
        records = load_import_records()
        records_for_source = records[self.source.name]
        pattern = self.filename_pattern()
        matched_records = [
            record
            for record in records_for_source
            if path_matches_pattern(record["imported_file"], pattern)
        ]
        sorted_records = sorted(
            matched_records, key=lambda record: record["imported_at"]
        )
        return [record["imported_file"] for record in sorted_records]

    def input_paths(self):
        """Return list of of paths to input files for task."""
        paths = glob.glob("%s/*/*" % self.source.data_dir)
        return sorted(
            path
            for path in paths
            if path_matches_pattern(path, self.filename_pattern())
        )

    def set_last_imported_path(self, path):
        """Set the path of the most recently imported data for this source."""
        now = datetime.datetime.now().replace(microsecond=0).isoformat()
        records = load_import_records()
        records[self.source.name].append({"imported_file": path, "imported_at": now})
        dump_import_records(records)

    def unimported_paths(self):
        """Return list of of paths to input files for task that have not been
        imported."""
        imported_paths = [record for record in self.imported_paths()]
        return [path for path in self.input_paths() if path not in imported_paths]


class ManualFetchTask(Task):
    def run(self, year, month):
        print("Running manual fetch task {}".format(self.name))
        instructions = self.manual_fetch_instructions()
        print(instructions)
        paths_before = find_files(self.source.data_dir)
        input("Press return when done, or to skip this step")
        paths_after = find_files(self.source.data_dir)
        new_paths = [path for path in paths_after if path not in paths_before]
        if new_paths:
            print("The following files have been manually fetched:")
            for path in new_paths:
                print(" * {}".format(path))
        else:
            print("No new files were found at {}".format(self.source.data_dir))
        input(
            "Press return to confirm, or Ctrl+C to cancel " "and resolve any problems"
        )

    def manual_fetch_instructions(self):
        source = self.source
        expected_location = os.path.join(
            settings.PIPELINE_DATA_BASEDIR, source.name, "YYYY_MM"
        )
        output = []
        output.append("~" * 80)
        output.append(
            "You should now locate the latest data for %s, if "
            "available" % source.name
        )
        output.append("You should save it at:")
        output.append("    %s" % expected_location)

        if source.index_url:
            output.append("Where to look:")
            output.append("    %s" % source.index_url)

        if source.urls:
            output.append("Previous data has been found at:")
            for k, v in source.urls.items():
                output.append("    %s: %s" % (k, v))

        if source.publication_schedule:
            output.append("Publication frequency:")
            output.append("    %s" % source.publication_schedule)

        if source.notes:
            output.append("Notes:")
            for line in textwrap.wrap(source.notes):
                output.append("    %s" % line)

        output.append("The last imported data can be found at:")
        for task in source.tasks_that_use_raw_source_data():
            paths = task.imported_paths()
            if paths:
                path = paths[-1]
            else:
                path = "<never imported>"
            output.append("    %s" % path)
        return "\n".join(output)


class AutoFetchTask(Task):
    def run(self, year, month):
        print("Running auto fetch task {}".format(self.name))
        command = self.command.format(year=year, month=month)
        tokens = shlex.split(command)
        call_command(*tokens)


class ConvertTask(Task):
    def run(self, year, month):
        # For now, year and month are ignored
        print("Running convert task {}".format(self.name))
        unimported_paths = self.unimported_paths()
        for path in unimported_paths:
            command = self.command.replace(self.filename_pattern(), path)
            tokens = shlex.split(command)
            call_command(*tokens)
            self.set_last_imported_path(path)


class ImportTask(Task):
    def run(self, year, month):
        # For now, year and month are ignored
        print("Running import task {}".format(self.name))
        unimported_paths = self.unimported_paths()
        for path in unimported_paths:
            command = self.command.replace(self.filename_pattern(), path)
            tokens = shlex.split(command)
            call_command(*tokens)
            self.set_last_imported_path(path)


class PostProcessTask(Task):
    def run(self, year, month, last_imported):
        # For now, year and month are ignored
        command = self.command.format(last_imported=last_imported)
        tokens = shlex.split(command)
        call_command(*tokens)


class TaskCollection(object):
    task_type_to_cls = {
        "manual_fetch": ManualFetchTask,
        "manual_fetch_skip_for_now": ManualFetchTask,
        "auto_fetch": AutoFetchTask,
        "convert": ConvertTask,
        "import": ImportTask,
        "post_process": PostProcessTask,
    }

    def __init__(self, task_data=None, ordered=False, task_type=None):
        self._tasks = {}
        if isinstance(task_data, dict):
            for name, attrs in task_data.items():
                cls = self.task_type_to_cls[attrs["type"]]
                task = cls(name, attrs)
                self.add(task)
        elif isinstance(task_data, list):
            for task in task_data:
                self.add(task)
        self._ordered = ordered
        self._type = task_type

    def add(self, task):
        self._tasks[task.name] = task

    def __getitem__(self, name):
        return self._tasks[name]

    def __iter__(self):
        if self._ordered:
            graph = nx.DiGraph()
            for task in self._tasks.values():
                graph.add_node(task)
                for dependency in task.dependencies:
                    graph.add_node(dependency)
                    graph.add_edge(dependency, task)
            tasks = nx.topological_sort(graph)
        else:
            tasks = [task for _, task in sorted(self._tasks.items())]

        for task in tasks:
            if self._type is None:
                yield task
            else:
                if self._type == task.task_type:
                    yield task

    def __bool__(self):
        if self._type:
            return any(task for task in self if task.task_type == self._type)
        else:
            return bool(self._tasks)

    def by_type(self, task_type):
        return TaskCollection(list(self), ordered=self._ordered, task_type=task_type)

    def ordered(self):
        return TaskCollection(list(self), ordered=True, task_type=self._type)


def load_tasks():
    metadata_path = settings.PIPELINE_METADATA_DIR

    with open(os.path.join(metadata_path, "sources.json")) as f:
        source_data = json.load(f)
    sources = SourceCollection(source_data)

    with open(os.path.join(metadata_path, "tasks.json")) as f:
        task_data = json.load(f)
    tasks = TaskCollection(task_data)

    for task in tasks:
        if task.source_id is None:
            task.set_source(None)
        else:
            source = sources[task.source_id]
            task.set_source(source)
            source.add_task(task)

        task.resolve_dependencies(tasks)

    return tasks


def load_import_records():
    with open(settings.PIPELINE_IMPORT_LOG_PATH) as f:
        log_data = json.load(f)
    return defaultdict(list, log_data)


def dump_import_records(records):
    with open(settings.PIPELINE_IMPORT_LOG_PATH, "w") as f:
        json.dump(records, f, indent=2, separators=(",", ": "))


def upload_all_to_storage(tasks):
    for task in tasks.by_type("convert"):
        upload_task_input_files(task)
    for task in tasks.by_type("import"):
        upload_task_input_files(task)


def upload_task_input_files(task):
    storage_client = StorageClient()
    bucket = storage_client.get_bucket()

    for path in task.input_paths():
        assert path[0] == "/"
        assert settings.PIPELINE_DATA_BASEDIR[-1] == "/"
        name = "hscic" + path.replace(settings.PIPELINE_DATA_BASEDIR, "/")
        blob = bucket.blob(name)
        if blob.exists():
            print("Skipping %s, already uploaded" % name)
            continue
        print("Uploading %s to %s" % (path, name))
        with open(path, "rb") as f:
            blob.upload_from_file(f)


def path_matches_pattern(path, pattern):
    return fnmatch.fnmatch(os.path.basename(path), pattern)


def call_command(*args):
    print("call_command {}".format(args))
    return django_call_command(*args)


def run_task(task, year, month, **kwargs):
    if TaskLog.objects.filter(
        year=year, month=month, task_name=task.name, status=TaskLog.SUCCESSFUL
    ).exists():
        # This task has already been run successfully
        return

    task_log = TaskLog.objects.create(year=year, month=month, task_name=task.name)

    try:
        task.run(year, month, **kwargs)
        task_log.mark_succeeded()
    except BaseException:
        # We want to catch absolutely every error here, including things that
        # wouldn't be caught by `except Exception` (like `KeyboardInterrupt`),
        # since we want to log that the task didn't complete.
        import traceback

        task_log.mark_failed(formatted_tb=traceback.format_exc())
        msg = "Importing data for {}_{} has failed when running {}.".format(
            year, month, task.name
        )
        notify_slack(msg, is_error=True)
        raise


def run_all(year, month, under_test=False):
    tasks = load_tasks()

    if not under_test:
        for task in tasks.by_type("manual_fetch"):
            run_task(task, year, month)

        for task in tasks.by_type("auto_fetch"):
            run_task(task, year, month)

    upload_all_to_storage(tasks)

    for task in tasks.by_type("convert").ordered():
        run_task(task, year, month)

    for task in tasks.by_type("import").ordered():
        run_task(task, year, month)

    prescribing_path = tasks["convert_hscic_prescribing"].imported_paths()[-1]
    last_imported = re.findall(r"/(\d{4}_\d{2})/", prescribing_path)[0]

    for task in tasks.by_type("post_process").ordered():
        run_task(task, year, month, last_imported=last_imported)

    if not under_test:
        # Remove numbers.json files.  These are created by check_numbers, and we want to
        # remove them after each import, since all numbers on the site will change with
        # the new data.  We only want to remove these files after importing real data,
        # and not during an end-to-end run.
        check_numbers_glob = os.path.join(
            settings.CHECK_NUMBERS_BASE_PATH, "*", "numbers.json"
        )
        for path in glob.glob(check_numbers_glob):
            os.remove(path)

    TaskLog.objects.create(
        year=year, month=month, task_name="fetch_and_import", status=TaskLog.SUCCESSFUL
    )

    msg = textwrap.dedent(
        f"""\
        Importing data for {year}_{month} complete!'

        You should now:

        * ask tech-support to run `sudo systemctl restart app.openprescribing.*.service` to pick up the new data
        * check that nothing looks horribly wrong with the data (https://openprescribing.net/national/england/ gives a good overview)
        * ask tech-support to send email notifications as described in https://github.com/ebmdatalab/openprescribing/wiki/Sending-monthly-email-alerts
        * send a tweet as described in https://github.com/ebmdatalab/openprescribing/wiki/Clinical-Informatician-Process-for-Updating-Data
        """
    )

    if not under_test:
        # Notify the openprescribing slack channel
        notify_slack(msg, channel="op")


def in_progress():
    try:
        current_year, current_month = (
            TaskLog.objects.values_list("year", "month")
            .distinct()
            .order_by("-year", "-month")[0]
        )
    except IndexError:
        # In development there might be no TaskLog objects.
        return False

    return not TaskLog.objects.filter(
        year=current_year, month=current_month, task_name="fetch_and_import"
    ).exists()

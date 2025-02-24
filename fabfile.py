import json
import os
import shlex
from datetime import datetime

from fabric.api import abort, env, prefix, run, settings, shell_env, sudo, task, warn
from fabric.context_managers import cd
from fabric.contrib.files import exists

env.hosts = ["web2.openprescribing.net"]
env.forward_agent = True
env.colorize_errors = True
env.user = "bennettbot"

environments = {"production": "openprescribing", "staging": "openprescribing_staging"}


def sudo_script(script, www_user=False):
    """Run script under `deploy/fab_scripts/` as sudo.

    We don't use the `fabric` `sudo()` command, because instead we
    expect the user that is running fabric to have passwordless sudo
    access.  In this configuration, that is achieved by the user being
    a member of the `fabric` group (see `setup_sudo()`, below).

    """
    if www_user:
        sudo_cmd = "sudo -u www-data "
    else:
        sudo_cmd = "sudo "
    return run(sudo_cmd + os.path.join(env.path, "deploy/fab_scripts/%s" % script))


def setup_sudo():
    """Ensures members of `fabric` group can execute deployment scripts as
    root without passwords

    """
    sudoer_file_test = "/tmp/openprescribing_fabric_{}".format(env.app)
    sudoer_file_real = "/etc/sudoers.d/openprescribing_fabric_{}".format(env.app)
    # Raise an exception if not set up
    check_setup = run(
        "/usr/bin/sudo -n {}/deploy/fab_scripts/test.sh".format(env.path),
        warn_only=True,
    )
    if check_setup.failed:
        # Test the format of the file, to prevent locked-out-disasters
        run(
            'echo "%fabric ALL = (root) NOPASSWD: {}/deploy/fab_scripts/" > {}'.format(
                env.path, sudoer_file_test
            )
        )
        run("/usr/sbin/visudo -cf {}".format(sudoer_file_test))
        # Copy it to the right place
        sudo("cp {} {}".format(sudoer_file_test, sudoer_file_real))


def git_init():
    run(
        "git init . && "
        "git remote add origin "
        "https://github.com/ebmdatalab/openprescribing.git && "
        "git fetch origin && "
        "git branch --set-upstream main origin/main"
    )


def venv_init():
    run("virtualenv .venv")


def git_pull():
    run("git fetch --all")
    run("git checkout --force origin/%s" % env.branch)


def pip_install():
    if "requirements.txt" in env.changed_files:
        with prefix("source .venv/bin/activate"):
            run("pip install -r requirements.txt")


def npm_install():
    installed = run("if [[ -n $(which npm) ]]; then echo 1; fi")
    if not installed:
        sudo(
            "curl -sL https://deb.nodesource.com/setup_20.x |"
            "bash - && apt-get install -y  "
            "nodejs binutils libproj-dev gdal-bin libgeoip1 libgeos-c1;",
            user=env.local_user,
        )
        sudo("npm install -g browserify && npm install -g eslint", user=env.local_user)


def npm_install_deps(force=False):
    if force or "openprescribing/media/js/package.json" in env.changed_files:
        run("cd openprescribing/media/js && npm install")


def npm_build_js():
    run("cd openprescribing/media/js && npm run build")


def npm_build_css(force=False):
    changed_css_files = [
        x for x in env.changed_files if x.startswith("openprescribing/media/css")
    ]
    if force or changed_css_files:
        run("cd openprescribing/media/js && npm run build-css")


def log_deploy():
    current_commit = run("git rev-parse --verify HEAD")
    url = "https://github.com/ebmdatalab/openprescribing/compare/%s...%s" % (
        env.previous_commit,
        current_commit,
    )
    log_line = json.dumps(
        {
            "started_at": str(env.started_at),
            "ended_at": str(datetime.utcnow()),
            "changes_url": url,
        }
    )
    run("echo '%s' >> deploy-log.json" % log_line)
    with prefix("source .venv/bin/activate"):
        run(
            "python deploy/notify_deploy.py {revision} {url} {fab_env}".format(
                revision=current_commit, url=url, fab_env=env.environment
            )
        )


def check_numbers():
    if env.environment != "production":
        return

    with prefix("source .venv/bin/activate"):
        run("cd openprescribing/ && python manage.py check_numbers")


def checkpoint(force_build):
    env.started_at = datetime.utcnow()
    with settings(warn_only=True):
        inited = run("git status").return_code == 0
        if not inited:
            git_init()
        if run("file .venv").return_code > 0:
            venv_init()
    env.previous_commit = run("git rev-parse --verify HEAD")
    run("git fetch")
    env.next_commit = run("git rev-parse --verify origin/%s" % env.branch)
    env.changed_files = set(
        run(
            "git diff --name-only %s %s" % (env.previous_commit, env.next_commit),
            pty=False,
        ).split()
    )
    if not force_build and env.next_commit == env.previous_commit:
        abort("No changes to pull from origin!")


def deploy_static():
    bootstrap_environ = {"MAILGUN_WEBHOOK_USER": "foo", "MAILGUN_WEBHOOK_PASS": "foo"}
    with shell_env(**bootstrap_environ):
        with prefix("source .venv/bin/activate"):
            run("cd openprescribing/ && python manage.py collectstatic -v0 --noinput")


def run_migrations():
    if env.environment == "production":
        with prefix("source .venv/bin/activate"):
            run("cd openprescribing/ && python manage.py migrate")
    else:
        warn("Refusing to run migrations in staging environment")


@task
def build_measures(environment=None, measures=None):
    setup_env_from_environment(environment)

    with cd(env.path):
        with prefix("source .venv/bin/activate"):
            # First we `--check` measures. This option validates all
            # the measures, rather than exiting at the first error,
            # which makes the debugging cycle shorter when dealing
            # with more than one
            run(
                "cd openprescribing/ && "
                "python manage.py import_measures --check "
                "--measure {}".format(measures)
            )
            print("Checks of measures passed")
            run(
                "cd openprescribing/ && "
                "python manage.py import_measures "
                "--measure {}".format(measures)
            )
            print("Rebuild of measures completed")


def build_changed_measures():
    """For any measures changed since the last deploy, run
    `import_measures`.
    """
    measures = []
    if env.environment == "production":
        # Production deploys are always one-off operations of tested
        # branches, so we can just check all the newly-changed files
        changed_files = env.changed_files
    else:
        # In staging, we often incrementally add commits and
        # re-test. In this case, we should rebuild all the changed
        # measures every time, because some of them may have failed to
        # have been built.

        # Git magic taken from https://stackoverflow.com/a/4991675/559140
        # finds the start of the current branch
        changed_files = run(
            "git diff --name-only "
            "$(diff --old-line-format='' --new-line-format='' "
            '<(git rev-list --first-parent "${1:-main}") '
            '<(git rev-list --first-parent "${2:-HEAD}") | head -1)',
            pty=False,
        ).splitlines()

    for f in changed_files:
        if "commands/measure_definitions" in f:
            measures.append(os.path.splitext(os.path.basename(f))[0])
    if measures:
        measures = ",".join(measures)
        print("Rebuilding measures {}".format(measures))
        build_measures(environment=env.environment, measures=measures)


def graceful_reload():
    result = sudo_script("graceful_reload.sh %s" % env.app)
    if result.failed:
        # Use the error from the bash command(s) rather than rely on
        # noisy (and hard-to-interpret) output from fabric
        abort(result)


def find_changed_static_files():
    changed = run(
        "find %s/openprescribing/static -type f -newermt '%s'"
        % (env.path, env.started_at.strftime("%Y-%m-%d %H:%M:%S"))
    ).split()
    return [x.replace(env.path + "/", "") for x in changed]


def setup_cron():
    crontab_path = "%s/deploy/crontab-%s" % (env.path, env.app)
    if exists(crontab_path):
        sudo_script("setup_cron.sh %s" % crontab_path)


def setup_env_from_environment(environment):
    if environment not in environments:
        abort("Specified environment must be one of %s" % ",".join(environments.keys()))
    env.app = environments[environment]
    env.environment = environment
    env.path = "/webapps/%s" % env.app


@task
def clear_cloudflare():
    if env.environment != "production":
        return

    with cd(env.path):
        with prefix("source .venv/bin/activate"):
            run("python deploy/clear_cache.py")


@task
def deploy(environment, force_build=False, branch="main"):
    setup_env_from_environment(environment)
    env.branch = branch
    setup_sudo()
    with cd(env.path):
        checkpoint(force_build)
        git_pull()
        pip_install()
        npm_install()
        npm_install_deps(force_build)
        npm_build_js()
        npm_build_css(force_build)
        deploy_static()
        run_migrations()
        # build_changed_measures()
        graceful_reload()
        clear_cloudflare()
        setup_cron()
        log_deploy()
        # check_numbers()


@task
def call_management_command(command_name, environment, *args, **kwargs):
    """Invokes management command in environment.

    Prints output of command.
    """

    cmd = "python openprescribing/manage.py {}".format(command_name)

    for arg in args:
        cmd += " {}".format(shlex.quote(arg))

    for k, v in kwargs.items():
        cmd += " --{}={}".format(shlex.quote(k), shlex.quote(v))

    setup_env_from_environment(environment)
    with cd(env.path):
        with prefix("source .venv/bin/activate"):
            print(run(cmd))


def notify_restart():
    with prefix("source .venv/bin/activate"):
        run("python deploy/notify_restart.py")


@task
def restart(environment):
    setup_env_from_environment(environment)
    with cd(env.path):
        graceful_reload()
        notify_restart()

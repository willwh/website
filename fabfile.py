import re
import os
import datetime

from invoke import task


LUTRIS_REMOTE = 'git@github.com:lutris/website.git'
DJANGO_SETTINGS_MODULE = 'lutrisweb.settings.production'


def get_config(context):
    try:
        host = context.host
    except AttributeError:
        host = 'localhost'

    if host == 'lutris.net':
        env = 'production'
    elif host == 'dev.lutris.net':
        env = 'staging'
    else:
        env = 'local'

    code_root = None
    if env == 'production':
        root = os.path.join('/srv/lutris')
        git_branch = 'master'
    elif env == 'staging':
        root = os.path.join('/srv/lutris_staging')
        git_branch = 'py3'
    else:
        root = os.path.dirname(os.path.abspath(__file__))
        code_root = root
        git_branch = ''

    return {
        'root': root,
        'code_root': code_root or os.path.join(root, host),
        'domain': host,
        'git_branch': git_branch,
        'env': env
    }


def activate(c):
    config = get_config(c)
    root = config['root']
    return c.prefix(
        'export DJANGO_SETTINGS_MODULE=%s && '
        '. %s/bin/envvars && '
        '. %s/bin/activate'
        % (DJANGO_SETTINGS_MODULE, root, root)
    )


@task
def touch_wsgi(c):
    """Touch wsgi file to trigger reload."""
    config = get_config(c)
    conf_dir = os.path.join(config['code_root'], 'config')
    with c.cd(conf_dir):
        c.run('touch lutrisweb.wsgi')


@task
def nginx_reload(c):
    """ reload Nginx on remote host """
    c.sudo('service nginx reload')


@task
def supervisor_restart(c):
    """ Reload Supervisor service """
    c.sudo('service supervisor restart')


@task
def test(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        with activate(c):
            c.run("python manage.py test accounts")


@task
def initial_setup(c):
    """Setup virtualenv"""
    config = get_config(c)
    c.sudo("mkdir -p %s" % config['root'])
    c.sudo("chown %s:%s %s" % (c.user, c.user, config['root']))
    c.sudo("apt install -y python3-venv")
    with c.cd(config['root']):
        c.run('python3 -m venv .')
        c.run('git clone %s %s' % (LUTRIS_REMOTE, config['domain']))


@task
def pip_list(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        with activate(c):
            c.run('pip list')


@task
def requirements(c, environment='production'):
    config = get_config(c)
    with c.cd(config['code_root']):
        with activate(c):
            c.run('pip install -U pip')
            c.run('pip install -U wheel')
            c.run(
                'pip install -r config/requirements/%s.pip --exists-action=s' % environment
            )


@task
def update_celery(c):
    config = get_config(c)
    tempfile = "/tmp/lutrisweb-celery.conf"
    c.local('cp config/lutrisweb-celery.conf ' + tempfile)
    c.local('sed -i s#%%ROOT%%#%(root)s#g ' % config + tempfile)
    c.local('sed -i s/%%DOMAIN%%/%(domain)s/g ' % config + tempfile)
    print('%(root)s' % config)
    c.put(tempfile, remote='%(root)s' % config)
    c.sudo(
        'mv %(root)s/lutrisweb-celery.conf '
        '/etc/supervisor/conf.d/%(domain)s-celery.conf' % config
    )


@task
def migrate(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        with activate(c):
            c.run("./manage.py migrate")


@task
def pull(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        c.run("git checkout %s" % config['git_branch'])
        c.run("git pull")
        c.run("git log")


@task
def npm(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        c.run("npm install -U bower")
        c.run("npm install")


def bower(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        c.run("bower install")


def grunt(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        c.run("grunt")


def collect_static(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        with activate(c):
            c.run('./manage.py collectstatic --noinput')


def fix_perms(c, user='www-data', group=None):
    if not group:
        group = user
    config = get_config(c)
    with c.cd(config['code_root']):
        c.sudo('chown -R %s:%s static' % (user, group))
        c.sudo('chmod -R ug+w static')
        c.sudo('chown -R %s:%s media' % (user, group))
        c.sudo('chmod -R ug+w media')


@task
def clean(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        c.run('py3clean .')


@task
def configtest(c):
    c.sudo("service nginx configtest")


@task
def authorize(c, ip):
    config = get_config(c)
    with c.cd(config['code_root']):
        with activate(c):
            c.run('./manage.py authorize %s' % ip)


def docs(c):
    config = get_config(c)
    with c.cd(config['code_root']):
        c.run("make client")
        with activate(c):
            c.run("make docs")


def sql_dump(c):
    sql_backup_dir = '/srv/backup/sql/'
    with c.cd(sql_backup_dir):
        backup_file = "lutris-{}.tar".format(
            datetime.datetime.now().strftime('%Y-%m-%d-%H-%M')
        )
        c.run('pg_dump --format=tar lutris > {}'.format(backup_file))
        c.run('gzip {}'.format(backup_file))
        backup_file += '.gz'
        c.get(backup_file, backup_file)


def sql_restore(c):
    """Restore a SQL DB dump to the local database"""
    db_dump = None
    for filename in os.listdir('.'):
        if re.match('lutris-.*\.tar.gz', filename):
            db_dump = filename
            break
    if not db_dump:
        print("No SQL dump found")
        return
    c.local('gunzip {}'.format(db_dump))
    db_dump = db_dump[:-3]
    c.local('pg_restore --clean --dbname=lutris {}'.format(db_dump))
    c.local('rm {}'.format(db_dump))


@task
def deploy(c):
    """Run a full deploy"""
    pull(c)
    bower(c)
    grunt(c)
    requirements(c)
    collect_static(c)
    migrate(c)
    docs(c)
    nginx_reload(c)
    update_celery(c)
    supervisor_restart(c)


@task
def pythonfix(c):
    """Apply a fix fro Python code only (no migration, no frontend change)"""
    pull(c)
    supervisor_restart(c)

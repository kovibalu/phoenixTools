#!/bin/python

###############################################################################
# python phoenixJobs.py --op=start|stop
#
#    Starts or stops (kills tmux session) jobs running on phoenix nodes.
#
# OPTIONS:
#
#
# Balazs Kovacs 2015


###############################################################################
# EDIT THIS TO CHANGE THE MACHINES WHICH ARE USED FOR EXECUTING JOBS

# basename prefix:
MACHINE_PREFIX = 'phoenix'

# id following prefix:
MACHINE_IDS = range(0, 11) + [13, 18, 19, 20, 23, 24, 25, 26]
#MACHINE_IDS = [10]
MACHINE_ID_FORMAT = '%02d'

# suffix following id:
#MACHINE_SUFFIX = '.cs.cornell.edu'
MACHINE_SUFFIX = ''

ROOT_PATH = '/home/bk472/projects/finegrained/code/'

# Docker settings
IMAGE_NAME = 'database.kmatzen.com:5000/bkovacs_opensurfaces'

MIN_MEM = 2000
# Tmux specific settings
# We start a tmux session for each docker command execution, so the user can easily kill them later
TMUX_SESSION = 'celery'
TMUX_HISTORY_LIMIT = '8000'
# If the tmux session already exists, kill that before starting another
KILL_EXISTING = False
# If this is true, we also stop the container when "stop" command is called
STOP_CONTAINER = False
# If true, we don't start more threads than the cpu count - current load
LOAD_LIMIT = False
# The commands which will be executed in the docker container for the different options
# Minimum amount of memory (in MB) needed per thread
DOCKER_START_CMD = 'cd /host/opensurfaces; ./scripts/start_queue_worker.sh {machine_name} {thread_count} intrinsic'


###############################################################################
# Implementation:

import argparse
import pipes
import subprocess


def qd(s):
    """ Quote a directory """
    if s is not None:
        s = str(s).strip()
        if s.startswith('~/') and '"' not in s and "'" not in s:
            return '"$HOME/%s"' % s[2:]
        else:
            return pipes.quote(s)
    else:
        return ''


def qs(s):
    """ Strip and quote-escape a string """
    if s is not None:
        s = str(s).strip()
        return pipes.quote(s)
    else:
        return ''


def run_remotely(machine_name, ssh_cmd, verbose=0):
    SSH_CMD_PREFIX = 'ssh -o StrictHostKeyChecking=no -o BatchMode=yes -o ConnectTimeout=30'
    cmd = '%s %s %s' % (SSH_CMD_PREFIX, machine_name, qs(ssh_cmd))
    if verbose > 1:
        print 'Running command: "%s"...' % cmd
    process = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE)
    output, errors = process.communicate()
    if errors:
        print 'STDERR:', errors

    return output


def run_on_docker(machine_name, container_id, session_cmd, tmux_session,
                  tmux_history_limit, kill_existing=True, verbose=0):
    start_cmd_list = []
    # Empty command means that we want to kill the existing process
    if kill_existing or not session_cmd:
        start_cmd_list += ["tmux kill-session -t %s" % qs(tmux_session)]

    if session_cmd:
        start_cmd_list += [
            "tmux new-session -s %s -d" % qs(tmux_session),
            "tmux set-option -t %s history-limit %s" % (qs(tmux_session), int(tmux_history_limit)),
            "tmux send -t %s %s ENTER" % (qs(tmux_session), qs(session_cmd)),
        ]
    docker_cmd = '; '.join(start_cmd_list)

    ssh_cmd = 'docker exec -d --privileged --user=ubuntu %s zsh -c "%s"' % (container_id, docker_cmd)
    # TODO: Super hack because of docker bug?
    #ssh_cmd = 'docker exec -ti --privileged --user=ubuntu %s script -q -c "%s" /dev/null' % (container_id, docker_cmd)
    return run_remotely(machine_name, ssh_cmd, verbose)


def get_container_id(machine_name, image_name, verbose=0):
    ssh_cmd = 'docker ps | grep %s | awk \'{print $1}\'' % image_name
    output = run_remotely(machine_name, ssh_cmd, verbose)
    return output.strip()


def get_mem_stats(machine_name, verbose=0):
    '''Gets used memory in megabytes on a certain machine'''
    ssh_cmd = 'free -m | grep + | awk \'{print $3 " " ($3+$4)}\''
    mem_stats = run_remotely(machine_name, ssh_cmd, verbose)
    used_memory, all_memory = [int(mem) for mem in mem_stats.split()]
    if verbose:
        print 'Used memory on %s: %dMB/%dMB' % (machine_name, used_memory, all_memory)

    return used_memory, all_memory


def get_cpu_stats(machine_name, verbose=0):
    '''Gets free memory in megabytes on a certain machine'''
    ssh_cmd = 'uptime | sed -r "s/^.*load average: (.*$)/\\1/" | tr "," " " | awk \'{print $1}\''
    load = float(run_remotely(machine_name, ssh_cmd, verbose))
    ssh_cmd = 'grep -c processor /proc/cpuinfo'
    cpu_num = int(run_remotely(machine_name, ssh_cmd, verbose))
    if verbose:
        print 'Load on %s: %.2f/%d' % (machine_name, load, cpu_num)

    return load, cpu_num


def start_container(machine_name, image_name, root_path, verbose=0):
    ssh_cmd = 'docker pull %s; cd %s; docker run -t -i -d -v /lib/modules:/lib/modules -v $PWD:/host --net=host --privileged %s zsh' % (
        image_name, qd(root_path), image_name,
    )
    return run_remotely(machine_name, ssh_cmd, verbose)


def stop_container(machine_name, container_id, verbose=0):
    ssh_cmd = 'docker stop %s' % container_id
    return run_remotely(machine_name, ssh_cmd, verbose)


def main(args):
    if args.op == 'start':
        msg = 'Starting jobs...'
        docker_cmd = DOCKER_START_CMD
    elif args.op == 'stop':
        msg = 'Stopping jobs...'
        # Empty command kills the tmux session
        docker_cmd = ''
    else:
        raise ValueError('Invalid operation: "%s"' % args.op)

    print msg

    for mid in MACHINE_IDS:
        format_str = '%%s%s%%s' % MACHINE_ID_FORMAT
        machine_name = format_str % (MACHINE_PREFIX, mid, MACHINE_SUFFIX)
        print 'Executing on %s...' % machine_name

        container_id = get_container_id(machine_name, IMAGE_NAME, args.verbose)

        # Stop the container if we have to and it's running
        if STOP_CONTAINER and not docker_cmd and container_id:
            stop_container(machine_name, container_id, args.verbose)
            container_id = get_container_id(machine_name, IMAGE_NAME, args.verbose)

        if not container_id:
            # If we wanted to kill it but it's not even running, we are done
            if not docker_cmd:
                continue

            print 'Docker container "%s" is not running, starting...' % IMAGE_NAME
            start_container(machine_name, IMAGE_NAME, ROOT_PATH, args.verbose)

            container_id = get_container_id(machine_name, IMAGE_NAME, args.verbose)
            if not container_id:
                print 'Failed to start docker container... Skipping this machine.'
                continue

        used_memory, all_memory = get_mem_stats(machine_name, args.verbose)
        load, cpu_num = get_cpu_stats(machine_name, args.verbose)

        max_thread_count = (all_memory - used_memory) / MIN_MEM
        max_thread_count = min(max_thread_count, cpu_num)
        if LOAD_LIMIT:
            max_thread_count = min(max_thread_count, cpu_num - round(load))
        max_thread_count = max(0, max_thread_count)
        print 'Max treads on %s: %d' % (machine_name, max_thread_count)

        if docker_cmd and max_thread_count == 0:
            print 'The machine is fully occupied, skipping...'
            continue

        subs_dic = {'machine_name': machine_name, 'thread_count': max_thread_count}
        subs_docker_cmd = docker_cmd.format(**subs_dic)
        run_on_docker(
            machine_name, container_id, subs_docker_cmd, TMUX_SESSION,
            TMUX_HISTORY_LIMIT, KILL_EXISTING, args.verbose
        )


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--op', required=True, choices=['start', 'stop'])
    parser.add_argument('--verbose', type=int, default=0)
    args = parser.parse_args()

    main(args)

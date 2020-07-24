"""
quick and dirty script to grab logs off contrail components 
and tar them up for quick retrieval.

Use at your own risk
"""

import argparse
import uuid
import re
import os
import pathlib
import subprocess
import tarfile
import shutil
import gzip
import yaml

CLI_MAP = {
    'control': 'contrail-controller',
    'analytics': 'contrail-analytics',
    'analyticsdb': 'contrail-analyticsdb',
    'vrouter': 'contrail-agent',
    'haproxy': 'contrail-haproxy',
    'heat': 'heat',
    'neutron': 'neutron',
    'appformix': 'appformix'
}


def cli_grab():
    """take stuff from cli, output it in a dict"""
    parser = argparse.ArgumentParser(description="Grab contrail component logs ")
    parser.add_argument("config_file", help="Location of YAML file containing log file paths"
                                            " and strings to hide if using '-h' option")
    parser.add_argument("component", help="Name of component: control, analytics, analyticsdb, "
                                          "vrouter, haproxy, heat, neutron, appformix")
    parser.add_argument("-i", "--device-ip", help="get the logs from the specified IP ")
    parser.add_argument("-f", "---ips-file", help="lookup component in the IPs file and grab logs "
                                                  "from all the IPs listed")
    parser.add_argument("-d", "--hide-data", action="store_true", help="attempt to obfuscate "
                                                                       "things like IPs, MACs & "
                                                                       "hostnames")
    parser.add_argument("-u", "--username", default='ubuntu', help="username, default= 'ubuntu' ")
    args = vars(parser.parse_args())
    return args


def read_config(file_path):
    with open(file_path) as file_handle:
        file_contents = yaml.load(file_handle.read())
    return file_contents


def get_remote_file(remote_ip, file_location, username, is_dir, destination):
    """grab the text contents of a file on a remote system via SCP."""
    try:
        if is_dir:
            print("getting remote logs in '{}' ".format(file_location))
            command = ['scp', '-r', '{}@{}:{}'.format(username, remote_ip, file_location), destination]
        else:
            print("getting remote log file '{}' ".format(file_location))
            command = ['scp', '{}@{}:{}'.format(username, remote_ip, file_location), destination]
        pipes = (subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
        std_out, std_err = pipes.communicate(timeout=20)           
    except subprocess.TimeoutExpired:
        print("No answer from '{}', skipping.".format(remote_ip))
    if pipes.returncode != 0:
        if b'No such file or directory' in std_err:
            print("Remote file or dir doesn't exist: '{}'".format(file_location))
        raise Exception(std_err.strip())



def iterate_devices(devices, logs, username, hide_data):
    run_id = str(uuid.uuid1())
    for device in devices:
        if hide_data:
            dev_name = 'X.X.' + '.'.join(device.split('.')[2:4])
        else:
            dev_name = device
        print("SCPing logs from '{}' ".format(device))
        file_path = "./tmp/{}/{}".format(run_id, dev_name) 
        pathlib.Path(file_path).mkdir(parents=True, exist_ok=True)
        for log_file in logs:
            if log_file[-1] == '/':
                is_dir = True
            else:
                is_dir = False
            get_remote_file(device, log_file, username, is_dir, file_path)
    return run_id


def read_zip(file_path):
    with gzip.open(file_path, 'rb')as zip_handle:
        file_contents = zip_handle.read()
    return file_contents


def read_log(file_path):
    with open(file_path, 'rb') as file_handle:
        file_contents = file_handle.read()
    return file_contents


def write_log(file_contents, file_path):
    with open(file_path, 'w') as file_handle:
        file_handle.write(file_contents)


def strip_pwds(dirty_text, host_re, dom_re):
    dirty_text = dirty_text.decode('utf-8')
    match_host = re.compile(host_re)
    match_domain = re.compile(dom_re)
    match_ip = re.compile(r'(\d{1,3}\.\d{1,3}\.)(\d{1,3}\.\d{1,3})', re.VERBOSE)
    match_mac = re.compile(r'(\w{2}:\w{2}:\w{2}:)(\w{2}:\w{2}:\w{2})', re.VERBOSE)
    clean_text = match_host.sub('dummy_host', dirty_text)
    clean_text = match_domain.sub('dummy.domain.com', clean_text)
    clean_text = match_ip.sub(r'X.X.\2', clean_text)
    clean_text = match_mac.sub(r'X:X:X:\2', clean_text)
    return clean_text



def remove_confidential(run_id, host_re, dom_re):
    working_dir = './tmp/' + run_id
    for root, _, files in os.walk(working_dir):
        for file_name in files:
            file_path = os.path.join(root, file_name)
            if re.match(r".+\.log\.[0-9]{1,2}\.gz", file_name):
                dirty_text = read_zip(file_path)
                print("removing confidential data from zip file '{}'".format(file_path))
                file_name = file_name[:-3]
            elif re.match(r".+\.log[.0-9]{0,3}", file_name):
                print("removing confidential data from text file '{}'".format(file_path))
                dirty_text = read_log(file_path)
            else:
                print("unsupported file: '{}' ignoring...".format(file_path))
            clean_text = strip_pwds(dirty_text, host_re, dom_re)
            file_path = '/'.join(file_path.split('/')[2:-1])
            pathlib.Path(file_path).mkdir(parents=True, exist_ok=True)
            write_log(clean_text, file_path + '/' + file_name)
    shutil.rmtree(working_dir)


def get_container_names(host, username):
    command = ['ssh', "{}@{}".format(username, host),  r"sudo docker ps --format {{.Names}}"]
    pipes = (subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    std_out, std_err = pipes.communicate(timeout=20)
    containers = std_out.decode('utf-8').splitlines()
    return containers     


def get_container_log(host, username, container):
    command = ['ssh', "{}@{}".format(username, host), "sudo cat", r"'$(sudo docker inspect -f {{.LogPath}})'"]
    pipes = (subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE))
    std_out, std_err = pipes.communicate(timeout=20)
    return std_out.decode('utf-8')


def iterate_containers(devices, username, run_id, hide_data):
    for host in devices:
        if hide_data:
            dev_name = 'X.X.' + '.'.join(host.split('.')[2:4])
        else:
            dev_name = host
        print("connecting to '{}' to get container logs".format(host))
        container_dir = "./tmp/{}/{}/container-logs".format(run_id, dev_name)
        pathlib.Path(container_dir).mkdir(parents=True, exist_ok=True)
        container_names = get_container_names(host, username)
        for container in container_names:
            container_log = get_container_log(host, username, container)
            print("getting logs for container '{}'".format(container))
            write_log(container_log, container_dir + '/' + container + '.json.log')


def final_zip(run_id, component):
    print("cleaning up temporary files and zipping up to '{}-{}-logs.tgz'".format(component, run_id))
    with tarfile.open(component + '-' + run_id + '-logs.tgz', 'w:gz') as tar:
        tar.add(run_id, os.path.basename(run_id)) 
    shutil.rmtree(run_id)


def main():
    """main script body"""
    args = cli_grab()
    config = read_config(args['config_file'])
    component = CLI_MAP[args['component']]
    log_files = config['components'][component]['logs']
    if args['device_ip'] and args['ips_file']:
        print("both device IP and IPs file specified.  Please only use one")
        exit()
    elif args['device_ip']:
        devices = [args['device_ip']]
    elif args['ips_file']:
        unit_ips = read_config(args['ips_file'])
        devices = unit_ips[component]
    run_id = iterate_devices(devices, log_files, args['username'], args['hide_data'])
    if config['components'][component]['containers']:
        iterate_containers(devices, args['username'], run_id,args['hide_data'])
    if args['hide_data']:
        remove_confidential(run_id,
                         config['filter_strings']['hostname_string'],
                         config['filter_strings']['domain_string']
                        )
    else:
        shutil.move('./tmp/' + run_id, run_id)
    final_zip(run_id, component)


if __name__ == '__main__':
    main()
#!/usr/bin/env python

import os
import sys
import imp
import time
import fuzzer
import shutil
import socket
import driller
import tarfile
import argparse
import importlib
import logging.config

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Shellphish fuzzer interface")
    parser.add_argument('binary', help="the path to the target binary to fuzz")
    parser.add_argument('-g', '--grease-with', help="A directory of inputs to grease the fuzzer with when it gets stuck.")
    parser.add_argument('-d', '--driller_workers', help="When the fuzzer gets stuck, drill with N workers.", type=int)
    parser.add_argument('-f', '--force_interval', help="Force greaser/fuzzer assistance at a regular interval (in seconds).", type=float)
    parser.add_argument('-w', '--work-dir', help="The work directory for AFL.", default="/dev/shm/work/")
    parser.add_argument('-c', '--afl-cores', help="Number of AFL workers to spin up.", default=1, type=int)
    parser.add_argument('-C', '--first-crash', help="Stop on the first crash.", action='store_true', default=False)
    parser.add_argument('-t', '--timeout', help="Timeout (in seconds).", type=float)
    parser.add_argument('-i', '--ipython', help="Drop into ipython after starting the fuzzer.", action='store_true')
    parser.add_argument('-T', '--tarball', help="Tarball the resulting AFL workdir for further analysis to this file -- '{}' is replaced with the hostname.")
    parser.add_argument('-m', '--helper-module', help="A module that includes some helper scripts for seed selection and such.")
    parser.add_argument('--memory', help="Memory limit to pass to AFL (MB, or use k, M, G, T suffixes)", default="8G")
    parser.add_argument('--no-dictionary', help="Do not create a dictionary before fuzzing.", action='store_true', default=False)
    parser.add_argument('--logcfg', help="The logging configuration file.", default=".shellphuzz.ini")
    parser.add_argument('-s', '--seed-dir', action="append", help="Directory of files to seed fuzzer with")
    parser.add_argument('--run-timeout', help="Number of seconds permitted for each run of binary", type=int)
    parser.add_argument('--driller-timeout', help="Number of seconds to allow driller to run", type=int, default=10*60)
    parser.add_argument('--length-extension', help="Try extending inputs to driller by this many bytes", type=int)
    args = parser.parse_args()

    if os.path.isfile(os.path.join(os.getcwd(), args.logcfg)):
        logging.config.fileConfig(os.path.join(os.getcwd(), args.logcfg))

    try: os.mkdir("/dev/shm/work/")
    except OSError: pass

    if args.helper_module:
        try:
            helper_module = importlib.import_module(args.helper_module)
        except (ImportError, TypeError):
            helper_module = imp.load_source('fuzzing_helper', args.helper_module)
    else:
        helper_module = None

    drill_extension = None
    grease_extension = None

    if args.grease_with:
        print ("[*] Greasing...")
        grease_extension = fuzzer.GreaseCallback(
            args.grease_with,
            grease_filter=helper_module.grease_filter if helper_module is not None else None,
            grease_sorter=helper_module.grease_sorter if helper_module is not None else None
        )
    print(args.driller_workers)
    if args.driller_workers:
        print ("[*] Drilling...")
        drill_extension = driller.LocalCallback(num_workers=args.driller_workers, worker_timeout=args.driller_timeout, length_extension=args.length_extension)

#    exit()
    stuck_callback = (
        (lambda f: (grease_extension(f), drill_extension(f))) if drill_extension and grease_extension
        else drill_extension or grease_extension
    )

    seeds = None
    if args.seed_dir:
        seeds = []
        print ("[*] Seeding...")
        for dirpath in args.seed_dir:
            for filename in os.listdir(dirpath):
                filepath = os.path.join(dirpath, filename)
                if not os.path.isfile(filepath):
                    continue
                with open(filepath, 'rb') as seedfile:
                    seeds.append(seedfile.read())

    print ("[*] Creating fuzzer...")
    fuzzer = fuzzer.Fuzzer(
        args.binary, args.work_dir, afl_count=args.afl_cores, force_interval=args.force_interval,
        create_dictionary=not args.no_dictionary, stuck_callback=stuck_callback, time_limit=args.timeout,
        memory=args.memory, seeds=seeds, timeout=args.run_timeout,
    )

    # start it!
    print ("[*] Starting fuzzer...")
    fuzzer.start()

    if args.ipython:
        print ("[!]")
        print ("[!] Launching ipython shell. Relevant variables:")
        print ("[!]")
        print ("[!] fuzzer")
        print ("[!] driller_extension")
        print ("[!] grease_extension")
        print ("[!]")
        import IPython; IPython.embed()

    try:
        print ("[*] Waiting for fuzzer completion (timeout: %s, first_crash: %s)." % (args.timeout, args.first_crash))

        crash_seen = False
        while True:
            time.sleep(5)
            if not crash_seen and fuzzer.found_crash():
                print ("[*] Crash found!")
                crash_seen = True
                if args.first_crash:
                    break
            if fuzzer.timed_out():
                print ("[*] Timeout reached.")
                break
    except KeyboardInterrupt:
        print ("[*] Aborting wait. Ctrl-C again for KeyboardInterrupt.")
    except Exception as e:
        print ("[*] Unknown exception received (%s). Terminating fuzzer." % e)
        fuzzer.kill()
        if drill_extension:
            drill_extension.kill()
        raise

    print ("[*] Terminating fuzzer.")
    fuzzer.kill()
    print(drill_extension)
 
    if drill_extension:
        print('get here')
        drill_extension.kill()

    if args.tarball:
        print ("[*] Dumping results...")
        p = os.path.join("/tmp/", "afl_sync")
        try:
            shutil.rmtree(p)
        except (OSError, IOError):
            pass
        shutil.copytree(fuzzer.out_dir, p)

        tar_name = args.tarball.replace("{}", socket.gethostname())

        tar = tarfile.open("/tmp/afl_sync.tar.gz", "w:gz")
        tar.add(p, arcname=socket.gethostname()+'-'+os.path.basename(args.binary))
        tar.close()
        print ("[*] Copying out result tarball to %s" % tar_name)
        shutil.move("/tmp/afl_sync.tar.gz", tar_name)

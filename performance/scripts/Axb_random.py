#!/usr/bin/env python3
#
###############################################################################
# Name:		Axb_random.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	December 20, 2017
# Last Changed: July 19, 2026
# Purpose:	Solve Ax=b when A and b are matrices populated with randomly
#		generated floats calculated from the normal distribution,
#		randomly generated standard deviations (sigma) between 0 and
#		sqrt(10), and an origin (mu) equal to zero. Provide options
#		for the user to generate a dump of the script's output to the
#		console, a text file containing A, b, x, and all sigma values,
#		and/or a CSV file for offline statistical analysis of the time
#		needed to compute the solution matrix x.
# Usage:	Axb_random.py [-h] --jobid JOBID --matrix-size MATRIX_SIZE
#                     [--console-dump CONSOLE_DUMP] [--create-csv CREATE_CSV]
#                     [--create-logs CREATE_LOGS]
# Notes:	Please refer to README.Axb_random.py for additional details.
###############################################################################

import time
start_time = time.time()

import argparse
import os
import platform
import sys

try:
    import numpy as np
    from scipy.linalg import solve
except ImportError as _e:
    import sys
    sys.exit(f"ERROR: Required package not found: {_e}\n  Install with: pip install numpy scipy")

exec_node = platform.node()
cwd = os.getcwd()

parser = argparse.ArgumentParser(description='Measures system performance by solving Ax=b using matrices and standard deviations randomly generated from the normal distribution')
parser.add_argument('--jobid', '-J', help='name of the job', required=True)
parser.add_argument('--matrix-size', '-M', help='set dimensions of the square matrix A', required=True, type=int)
parser.add_argument('--console-dump', '-D', help='print A, b, x, and all sigma values to stdout', required=False, default='yes')
parser.add_argument('--create-csv', '-C', help='create a CSV data file for offline analysis', required=False, default='yes')
parser.add_argument('--create-logs', '-L', help='print console_dump output to a text file', required=False, default='yes')
parser.add_argument('--note', '-N', help='short description of the test (no commas)', required=False, default='standalone')
args = parser.parse_args()

cluster_jobid  = args.jobid
matrix_size    = args.matrix_size
console_dump   = args.console_dump.lower() == 'yes'
create_csv     = args.create_csv.lower() == 'yes'
create_logs    = args.create_logs.lower() == 'yes'
note           = args.note

if not console_dump and not create_logs and not create_csv:
    print("")
    print("*******************************************************************")
    print("**  console_dump, create_csv, and create_logs are all disabled.  **")
    print("**  Axb_random.py won't do anything useful if invoked like this! **")
    print("*******************************************************************")
    print("")
    print("Please rerun the script so it generates some form of output.")
    sys.exit(1)

np.set_printoptions(threshold=sys.maxsize, suppress=True, precision=4,
                    formatter={'float': '{: 0.4f}'.format})
mu      = 0
sigma_A = np.random.uniform(0, np.sqrt(10), 1)
sigma_b = np.random.uniform(0, np.sqrt(10), 1)
A       = np.random.normal(mu, sigma_A, (matrix_size, matrix_size))
b       = np.random.normal(mu, sigma_b, matrix_size)
x       = solve(A, b)

if console_dump:
    print("")
    print("                             --------------")
    print("                             -  Matrix A  -")
    print("                             --------------")
    print("")
    print(A)
    print("")
    print("Sigma value for matrix A =", format(*sigma_A, '.4f'))
    print("")
    print("                             --------------")
    print("                             -  Matrix b  -")
    print("                             --------------")
    print("")
    print(b)
    print("")
    print("Sigma value for matrix b =", format(*sigma_b, '.4f'))
    print("")
    print("                          -----------------------")
    print("                          -  Solution matrix x  -")
    print("                          -----------------------")
    print("")
    print(x)
    print("")

if create_logs:
    with open(cluster_jobid + ".log", "w") as lf:
        lf.write("\n")
        lf.write("                             --------------\n")
        lf.write("                             -  Matrix A  -\n")
        lf.write("                             --------------\n\n")
        lf.write(str(A) + "\n\n")
        lf.write(f"Sigma value for matrix A = {format(*sigma_A, '.4f')}\n\n")
        lf.write("                             --------------\n")
        lf.write("                             -  Matrix b  -\n")
        lf.write("                             --------------\n\n")
        lf.write(str(b) + "\n\n")
        lf.write(f"Sigma value for matrix b = {format(*sigma_b, '.4f')}\n\n")
        lf.write("                          -----------------------\n")
        lf.write("                          -  Solution matrix x  -\n")
        lf.write("                          -----------------------\n\n")
        lf.write(str(x) + "\n\n")

end_time     = time.time()
elapsed_time = round(end_time - start_time, 4)

SEP = "--------------------------------------------------------------------------"

def _print_summary(f=None):
    lines = [
        SEP,
        "                                Job Summary",
        SEP,
        f"  Execute Node     =  {exec_node}",
        f"  Description      =  {note}",
    ]
    if cluster_jobid:
        lines.append(f"  Cluster JobID    =  {cluster_jobid}")
    if create_logs:
        lines.append(f"  Log File Path    =  {cwd}/{cluster_jobid}.log")
    if create_csv:
        lines.append(f"  Data File Path   =  {cwd}/{cluster_jobid}.csv")
    lines += [
        f"  Matrix Size      =  {matrix_size} x {matrix_size}",
        SEP,
        f"  Start Time       =  {time.strftime('%a, %d %b %Y @ %H:%M:%S', time.localtime(start_time))}",
        f"  End Time         =  {time.strftime('%a, %d %b %Y @ %H:%M:%S', time.localtime(end_time))}",
        f"  Time Elapsed     =  {elapsed_time:.4f} seconds",
        SEP,
    ]
    out = "\n".join(lines) + "\n"
    if f:
        f.write(out)
    else:
        print(out)

if console_dump or (not create_logs and create_csv):
    _print_summary()

if create_logs:
    with open(cluster_jobid + ".log", "a") as lf:
        _print_summary(lf)
    if console_dump:
        print("Finished creating the log file.")

if create_csv:
    with open(cluster_jobid + ".csv", "w") as cf:
        cf.write(f"{exec_node},{cluster_jobid},{matrix_size},{elapsed_time},{note}\n")
    if console_dump:
        print("Finished creating the CSV data file.")

if console_dump:
    print("Exiting...")
sys.exit(0)

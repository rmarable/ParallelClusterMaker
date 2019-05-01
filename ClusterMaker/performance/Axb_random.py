#!/usr/bin/env python3
#
###############################################################################
# Name:		Axb_random.py
# Author:	Rodney Marable <rodney.marable@gmail.com>
# Created On:	December 20, 2017
# Last Changed: April 28, 2019
# Purpose:	Solve Ax=b when A and b are matrices populated with randomly
#		generated floats calculated from the normal distribution,
#		randomly generated standard deviations (sigma) between 0 and
#		sqrt(10), and an origin (mu) equal to zero. Provide options
#		for the user to generate a dump of the script's output to the
#		console, a text file containing A, b, x, and all sigma values,
#		and/or a CSV file for offline stastical analysis of the time
#		needed to compute the solution matrix x.
# Usage:	Axb_random.py [-h] --jobid JOBID --matrix-size MATRIX_SIZE
#                     [--console-dump CONSOLE_DUMP] [--create-csv CREATE_CSV]
#                     [--create-logs CREATE_LOGS]
# Notes:	Please refer to README.Axb_random.py for additional details.
###############################################################################
###############################################################################
#                                                                             #
#      WARNING: There are no user-configurable options in this script.        #
#  Don't change anything below this line unless you know what you are doing!  #
#                                                                             #
###############################################################################
###############################################################################
#
# Start the job timer.

import time
start_time=time.time()

# Import the rest of the required Python libraries.

import argparse
import gzip
import os
import math
import numpy as np
import platform
import shutil
import sys
from scipy.linalg import solve

# Get the testing instance hostname and the current working directory.

exec_node = platform.uname()[1]
cwd = os.getcwd()

# Parse values for cluster_jobid, matrix_size, console_dump, create_csv, and
# create_logs from the command line.

parser = argparse.ArgumentParser(description='Measures system performance by solving Ax=b using matrices and standard deviations randomly generated from the normal distribution')
parser.add_argument('--jobid', '-J', help='name of the job - used to determine CONSOLE_DUMP, CONSOLE_LOG, and CSV_DATA file names', required=True)
parser.add_argument('--matrix-size', '-M', help='set dimensions of the square matrix A', required=True, type=int)
parser.add_argument('--console-dump', '-D', help='print A, b, x, and all sigma values to stdout', required=False, default='yes')
parser.add_argument('--create-csv', '-C', help='create a CSV data file for offline analysis', required=False, default='yes')
parser.add_argument('--create-logs', '-L', help='print console_dump output to a text file', required=False, default='yes')
parser.add_argument('--note', '-N', help='short description of the test to be included as a field in the CSV data file - do *not* use commas with this option', required=False, default='standalone')
args = parser.parse_args()
cluster_jobid = args.jobid
matrix_size = args.matrix_size
console_dump = args.console_dump
create_csv = args.create_csv
create_logs = args.create_logs
note = args.note

# Exit if the user disables console_dump, create_csv, and create_logs.

if (console_dump) == "no" and (create_logs) == "no" and (create_csv) == "no":
    print("")
    print("*******************************************************************")
    print("*******************************************************************")
    print("**                                                               **")
    print("**  console_dump, create_csv, and create_logs are all disabled.  **")
    print("**  Axb_random.py won't do anything useful if invoked like this! **")
    print("**                                                               **")
    print("*******************************************************************")
    print("*******************************************************************")
    print("")
    print("Please rerun the script so it generates some form of output.")
    print("Aborting...")
    sys.exit(1)

# Configure Numpy to print all matrices without using scientific notation and
# limit each element to 4 decimal points.
# Set the origin (mu) to zero.
# Generate A, b, and their standard deviations from the normal distribution.
# Solve Ax=b using the Scipy linear algebra routines.

np.set_printoptions(threshold=np.inf,suppress=True,precision=4,formatter={'float': '{: 0.4f}'.format})
mu = 0
sigma_A = np.random.uniform(0, math.sqrt(10), 1)
sigma_b = np.random.uniform(0, math.sqrt(10), 1)
A = np.random.normal(mu, sigma_A, (matrix_size,matrix_size))
b = np.random.normal(mu, sigma_b, matrix_size)
x = solve(A, b)

# Print A, b, x, and all sigma values to stdout if console_dump is enabled.

if (console_dump) == "yes":
    print("")
    print("                             --------------")
    print("                             -  Matrix A  -")
    print("                             --------------")
    print("")
    print(A)
    print("")
    print("Sigma value for matrix A =", format(*sigma_A,'.4f'))
    print("")
    print("                             --------------")
    print("                             -  Matrix b  -")
    print("                             --------------")
    print("")
    print(b)
    print("")
    print("Sigma value for matrix b =", format(*sigma_b,'.4f'))
    print("")
    print("                          -----------------------")
    print("                          -  Solution matrix x  -")
    print("                          -----------------------")
    print("")
    print(x)
    print("")

# Dump A, b, x, all sigma values, and a summary report containing timing data
# to a text file (JOBID.log) if create_logs is enabled.

if (create_logs) == "yes":
    print("", file=open(cluster_jobid + ".log", "w"))
    print("                             --------------", file=open(cluster_jobid + ".log", "a"))
    print("                             -  Matrix A  -", file=open(cluster_jobid + ".log", "a"))
    print("                             --------------", file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))
    print(A, file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))
    print("Sigma value for matrix A =", format(*sigma_A,'.4f'), file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))
    print("                             --------------", file=open(cluster_jobid + ".log", "a"))
    print("                             -  Matrix b  -", file=open(cluster_jobid + ".log", "a"))
    print("                             --------------", file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))
    print(b, file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))
    print("Sigma value for matrix b =", format(*sigma_b,'.4f'), file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))
    print("                          -----------------------", file=open(cluster_jobid + ".log", "a"))
    print("                          -  Solution matrix x  -", file=open(cluster_jobid + ".log", "a"))
    print("                          -----------------------", file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))
    print(x, file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))

# Stop the job timer.

end_time=time.time()
elapsed_time=round(end_time-start_time,4)

# Print the job summary to the console if console_dump is enabled.

if (console_dump == "yes"):
    print("--------------------------------------------------------------------------")
    print("                                Job Summary")
    print("--------------------------------------------------------------------------")
    print("  Execute Node     =  %s " % (str(exec_node)))
    print("  Description      =  %s " % (str(note)))
    if len(cluster_jobid) != 0:
        print("  Cluster JobID    =  %s  " % str(cluster_jobid))
    if (create_logs) == "yes":
        print("  Log File Path    =  %s  " % str(cwd + '/' + cluster_jobid + ".log"))
    if (create_csv) == "yes":
        print("  Data File Path   =  %s  " % str(cwd + '/' + cluster_jobid + ".csv"))
    print("  Matrix Size      =  %s x %s  " % (str(matrix_size), str(matrix_size)))
    print("--------------------------------------------------------------------------")
    print("  Start Time       =  %s " % time.strftime("%a, %d %b %Y @ %H:%M:%S", time.localtime(start_time)))
    print("  End Time         =  %s " % time.strftime("%a, %d %b %Y @ %H:%M:%S", time.localtime(end_time)))
    print("  Time Elapsed     =  %.4f seconds  " % elapsed_time)
    print("--------------------------------------------------------------------------")

# If a logfile was requested, include the path and append the job summary.

if (create_logs) == "yes":
    print("--------------------------------------------------------------------------", file=open(cluster_jobid + ".log", "a"))
    print("                                Job Summary", file=open(cluster_jobid + ".log", "a"))
    print("--------------------------------------------------------------------------", file=open(cluster_jobid + ".log", "a"))
    print("  Execute Node     =  %s " % (str(exec_node)), file=open(cluster_jobid + ".log", "a"))
    print("  Description      =  %s " % (str(note)), file=open(cluster_jobid + ".log", "a"))
    if len(cluster_jobid) != 0:
        print("  Cluster JobID    =  %s  " % str(cluster_jobid), file=open(cluster_jobid + ".log", "a"))
    print("  Logfile Path     =  %s  " % str(cwd + '/' + cluster_jobid + ".log"), file=open(cluster_jobid + ".log", "a"))
    if (create_csv) == "yes":
        print("  Data File Path   =  %s  " % str(cwd + '/' + cluster_jobid + ".csv"), file=open(cluster_jobid + ".log", "a"))
    print("  Matrix Size      =  %s x %s  " % (str(matrix_size), str(matrix_size)), file=open(cluster_jobid + ".log", "a"))
    print("--------------------------------------------------------------------------", file=open(cluster_jobid + ".log", "a"))
    print("  Start Time       =  %s " % time.strftime("%a, %d %b %Y @ %H:%M:%S", time.localtime(start_time)), file=open(cluster_jobid + ".log", "a"))
    print("  End Time         =  %s " % time.strftime("%a, %d %b %Y @ %H:%M:%S", time.localtime(end_time)), file=open(cluster_jobid + ".log", "a"))
    print("  Time Elapsed     =  %.4f seconds  " % elapsed_time, file=open(cluster_jobid + ".log", "a"))
    print("--------------------------------------------------------------------------", file=open(cluster_jobid + ".log", "a"))
    print("", file=open(cluster_jobid + ".log", "a"))
    if (console_dump) == "yes":
        print("")
        print("Finished creating the log file.")
    if (create_csv) == "yes":
        print(exec_node, ',', cluster_jobid, ',', matrix_size, ',', elapsed_time, ',', note, sep='', file=open(cluster_jobid + ".csv", "w"))
    if (console_dump) == "yes":
        print("Finished creating the CSV data file.")

# Generate a CSV file if no logs or console dump were requested.
# Print a jub summary including the path to the CSV file.

if (console_dump) == "no" and (create_logs) == "no" and (create_csv) == "yes":
    print("")
    print("--------------------------------------------------------------------------")
    print("                                Job Summary")
    print("--------------------------------------------------------------------------")
    print("  Execute Node     =  %s " % (str(exec_node)))
    print("  Description      =  %s " % (str(note)))
    if len(cluster_jobid) != 0:
        print("  Cluster JobID    =  %s  " % str(cluster_jobid))
    print("  Data File Path   =  %s  " % str(cwd + '/' + cluster_jobid + ".csv"))
    print("  Matrix Size      =  %s x %s  " % (str(matrix_size), str(matrix_size)))
    print("--------------------------------------------------------------------------")
    print("  Start Time       =  %s " % time.strftime("%a, %d %b %Y @ %H:%M:%S", time.localtime(start_time)))
    print("  End Time         =  %s " % time.strftime("%a, %d %b %Y @ %H:%M:%S", time.localtime(end_time)))
    print("  Time Elapsed     =  %.4f seconds  " % elapsed_time)
    print("--------------------------------------------------------------------------")
    print(exec_node, ',', cluster_jobid, ',', matrix_size, ',', elapsed_time, ',', note, sep='', file=open(cluster_jobid + ".csv", "w"))

# Cleanup and exit.

if (console_dump) == "yes":
    print("Exiting...")
sys.exit(0)
